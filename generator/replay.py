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


class EventstreamSink:
    """Next issue: send JSON events to an Eventstream custom endpoint (Event Hub protocol, azure-eventhub),
    connection string from EVENTSTREAM_<ENV>_<WMS|CARRIER>_CONNECTION_STRING, batches of <= 500, partition key = order_id."""

    def send(self, events: list[Event]) -> None:
        raise NotImplementedError("EventstreamSink is designed in generator/README.md; built in the replayer issue")


# ------------------------------------------------------------------ order windows + CLI (#26; virtual clock in #28)
def order_window(tables: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Shop rows for orders purchased in [start, end), plus every customer, product and seller they reference."""
    soh = tables["sales_order_header"]
    orders = soh[(soh["purchase_ts"] >= start) & (soh["purchase_ts"] < end)]
    ids = set(orders["order_id"])
    items = tables["sales_order_item"][tables["sales_order_item"]["order_id"].isin(ids)]
    return {
        "customer": tables["customer"][tables["customer"]["customer_id"].isin(set(orders["customer_id"]))],
        "material": tables["material"][tables["material"]["material_id"].isin(set(items["material_id"]))],
        "vendor": tables["vendor"][tables["vendor"]["vendor_id"].isin(set(items["vendor_id"]))],
        "sales_order_header": orders,
        "sales_order_item": items,
        "payment": tables["payment"][tables["payment"]["order_id"].isin(ids)],
        "review": tables["review"][tables["review"]["order_id"].isin(ids)],
    }


def feature_sqldb(agent: str):
    """Server, database and credential for sqldb_shop in the agent's feature workspace."""
    import requests

    from generator.onelake import credential

    cred = credential(agent)
    h = {"Authorization": "Bearer " + cred.get_token("https://api.fabric.microsoft.com/.default").token}
    api = "https://api.fabric.microsoft.com/v1"
    ws = next(w["id"] for w in requests.get(f"{api}/workspaces", headers=h, timeout=60).json()["value"]
              if w["displayName"] == f"shoppulse-feat-{agent}")
    db = next(d for d in requests.get(f"{api}/workspaces/{ws}/sqlDatabases", headers=h, timeout=60).json()["value"]
              if d["displayName"] == "sqldb_shop")
    return db["properties"]["serverFqdn"], db["properties"]["databaseName"], cred


def main() -> None:
    import argparse
    import json
    import pathlib

    from generator import OUT
    from generator.sqlsink import SqlSink

    ap = argparse.ArgumentParser(description="ShopPulse replayer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sql-load", help="MERGE the shop rows of one order window into the agent's sqldb_shop")
    s.add_argument("--agent", required=True, choices=["mgr", "de1", "de2", "bi", "ds"])
    s.add_argument("--from", dest="start", required=True, type=pd.Timestamp)
    s.add_argument("--to", dest="end", required=True, type=pd.Timestamp)
    s.add_argument("--dir", type=pathlib.Path, default=OUT / "sample_0.05")
    a = ap.parse_args()

    tables = {n: pd.read_parquet(a.dir / f"{n}.parquet") for n in
              ["customer", "material", "vendor", "sales_order_header", "sales_order_item", "payment", "review"]}
    win = order_window(tables, a.start, a.end)
    server, database, cred = feature_sqldb(a.agent)
    sink = SqlSink(server, database, cred).connect()
    try:
        result = sink.write_all(win)
    finally:
        sink.close()
    print(json.dumps({"window": [str(a.start), str(a.end)], "rows": {k: len(v) for k, v in win.items()}, "merge": result}, indent=1))


if __name__ == "__main__":
    main()
