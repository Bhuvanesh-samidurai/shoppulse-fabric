"""Live replayer (owner: DE1). Design + the deterministic event logic; sinks are stubs for the next issue.

Turns the relocated shop tables into a live stream for one environment (SPEC §2):

    shop rows   --insert/update-->  sqldb_shop (mirrored to Bronze by Fabric)
    WMS events  --send-->           Eventstream es_wms      -> bronze.wms_event + Eventhouse
    carrier ev. --send-->           Eventstream es_carrier  -> bronze.carrier_event + Eventhouse

Timeline: every row and event has a business timestamp. The replayer walks a virtual clock from
--start at --speed x real time and emits everything whose timestamp is <= the clock. Dev uses a 5%
sample at 60x (one Olist day every 24 minutes); Test/Prod use the full set at 1x-60x.

Idempotency: event_id = uuid5(order_id|item_no|type), SQL writes are MERGE on the primary key, so
restarting the replayer or replaying a window produces no duplicates downstream (Silver dedups on event_id).

F2 rule (SPEC §4): only one environment receives live data at a time; stop the replayer otherwise.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import uuid

import pandas as pd

NS = uuid.UUID("2f0b8a1e-7c4d-4c39-9a51-6c7d5e8f9a10")
CARRIERS = {"Nord": ["DHL", "HERMES", "DPD"], "Ost": ["DHL", "DPD", "HERMES"], "West": ["DPD", "DHL", "HERMES"], "Sued": ["DHL", "DPD", "HERMES"]}
CARRIER_WEIGHTS = [0.55, 0.25, 0.20]
DELAY_REASONS = ["weather", "depot_backlog", "address_issue", "vehicle_breakdown", "peak_volume"]


def _u(key: str) -> float:
    """Deterministic uniform [0, 1) per key, so a replay produces identical events."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / 16**12


def event_id(*parts) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def carrier_for(order_id: str, macro_region: str) -> str:
    x, acc = _u(f"carrier|{order_id}"), 0.0
    for code, w in zip(CARRIERS[macro_region], CARRIER_WEIGHTS, strict=True):
        acc += w
        if x < acc:
            return code
    return CARRIERS[macro_region][0]


@dataclasses.dataclass(frozen=True)
class Event:
    stream: str          # "wms" | "carrier"
    payload: dict        # matches contracts/bronze/{wms_event,carrier_event}.yml (without _ingested_at/_source/_batch_id)

    @property
    def ts(self) -> dt.datetime:
        return self.payload["event_ts"]


def order_events(order: pd.Series, items: pd.DataFrame, postal_code: str, macro_region: str, review_score: int | None = None) -> list[Event]:
    """All WMS and carrier events for one order, derived from its real Olist timestamps.

    approved -> PICK (+1..6 h, before handover) -> PACK (+10..60 min) -> LABEL_CREATED (+5 min)
    -> SHIP + PICKED_UP (carrier_handover_ts) -> IN_TRANSIT (+4..12 h) -> OUT_FOR_DELIVERY (-2..6 h) -> DELIVERED.
    Late deliveries carry a delay_reason. Some 1-star orders are returned 5..14 days after delivery.
    """
    oid, wh = order["order_id"], order["warehouse_code"]
    approved, handover, delivered = order["approved_ts"], order["carrier_handover_ts"], order["delivered_ts"]
    if order["order_status"] in ("canceled", "unavailable", "created") or pd.isna(approved):
        return []
    events: list[Event] = []

    def wms(kind, ts, item, qty=1, reason=None):
        events.append(Event("wms", {"event_id": event_id(oid, item["item_no"], kind), "event_type": kind, "event_ts": ts, "warehouse_code": wh,
                                    "order_id": oid, "item_no": int(item["item_no"]), "material_id": item["material_id"], "quantity": qty,
                                    "reason_code": reason, "event_date": ts.date()}))

    latest_pick = handover - pd.Timedelta(minutes=30) if pd.notna(handover) else approved + pd.Timedelta(hours=24)
    for _, it in items.iterrows():
        k = f"{oid}|{it['item_no']}"
        pick = min(approved + pd.Timedelta(hours=1 + 5 * _u("pick|" + k)), max(latest_pick, approved))
        wms("PICK", pick, it)
        wms("PACK", pick + pd.Timedelta(minutes=10 + 50 * _u("pack|" + k)), it)
        if pd.notna(handover):
            wms("SHIP", handover, it)

    if pd.isna(handover):
        return sorted(events, key=lambda e: e.ts)
    carrier = carrier_for(oid, macro_region)
    tracking = "JJD" + hashlib.sha1(oid.encode()).hexdigest()[:14].upper()
    late = pd.notna(delivered) and delivered.normalize() > pd.Timestamp(order["estimated_delivery_date"])
    reason = DELAY_REASONS[int(_u("delay|" + oid) * len(DELAY_REASONS))] if late else None

    def car(status, ts, plz=None, delay=None):
        events.append(Event("carrier", {"event_id": event_id(oid, "carrier", status), "tracking_id": tracking, "order_id": oid,
                                        "carrier_code": carrier, "status": status, "event_ts": ts, "postal_code": plz,
                                        "delay_reason": delay, "event_date": ts.date()}))

    car("LABEL_CREATED", handover - pd.Timedelta(minutes=5))
    car("PICKED_UP", handover)
    car("IN_TRANSIT", handover + pd.Timedelta(hours=4 + 8 * _u("transit|" + oid)), delay=reason)
    if pd.notna(delivered):
        out_for_delivery = max(delivered - pd.Timedelta(hours=2 + 4 * _u("ofd|" + oid)), handover + pd.Timedelta(minutes=30))
        car("OUT_FOR_DELIVERY", out_for_delivery, plz=postal_code)
        car("DELIVERED", delivered, plz=postal_code, delay=reason)
        if review_score == 1 and _u("return|" + oid) < 0.4:
            back = delivered + pd.Timedelta(days=5 + 9 * _u("retday|" + oid))
            for _, it in items.iterrows():
                wms("RETURN_RECEIPT", back, it, reason="customer_dissatisfied")
    return sorted(events, key=lambda e: e.ts)


class SqlSink:
    """Next issue: MERGE shop rows into sqldb_shop (mssql-python or pyodbc, token auth as the agent/CI SP),
    batched per virtual minute, ordered parent-first (customer, material, vendor, header, item, payment, review)."""

    def write(self, table: str, rows: pd.DataFrame) -> None:
        raise NotImplementedError("SqlSink is designed in generator/README.md; built in the replayer issue")


class EventstreamSink:
    """Next issue: send JSON events to an Eventstream custom endpoint (Event Hub protocol, azure-eventhub),
    connection string from EVENTSTREAM_<ENV>_<WMS|CARRIER>_CONNECTION_STRING, batches of <= 500, partition key = order_id."""

    def send(self, events: list[Event]) -> None:
        raise NotImplementedError("EventstreamSink is designed in generator/README.md; built in the replayer issue")
