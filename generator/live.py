"""Live replay: virtual clock + checkpoint/resume (owner: DE1, issue #28).

    python -m generator.replay run --agent de1 --start 2025-08-01 --speed 1440 --minutes 10
    python -m generator.replay run --agent de1 --resume --speed 1440 --minutes 10

How it works
- A virtual clock starts at --start (or at the checkpoint with --resume) and advances --speed x real time.
- Every tick (--tick seconds) it emits everything whose business timestamp falls in [last, now):
    * SQL (SqlSink MERGE): new orders with items, payments, customers, products, sellers at purchase_ts;
      order headers again whenever a milestone happens (approved, handed to carrier, delivered), with
      later milestones still NULL and the status as it was at that moment; reviews at created_ts.
    * Events (EventstreamSink): WMS and carrier events with event_ts in the window.
- After SQL and events of a tick both succeed, the checkpoint (generator/data/state/<agent>.json) moves to
  the window end. If anything fails (capacity paused, network), the run stops WITHOUT moving the checkpoint.
  --resume replays that window: SQL is MERGE and Bronze dedups on event_id, so nothing is lost or doubled.
  The KQL hot path may see a replayed window twice; KQL queries count distinct event_id.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pandas as pd

from generator import DATA
from generator.replay import order_window, window_events

STATE = DATA / "state"
MILESTONES = ["purchase_ts", "approved_ts", "carrier_handover_ts", "delivered_ts"]


def header_snapshot(headers: pd.DataFrame, vt: pd.Timestamp) -> pd.DataFrame:
    """Order headers as they looked at virtual time vt: future milestones NULL, status of that moment."""
    h = headers.copy()
    for c in MILESTONES[1:]:
        h.loc[h[c] > vt, c] = pd.NaT
    final = headers["order_status"]
    status = pd.Series("created", index=h.index)
    status[h["approved_ts"].notna()] = "approved"
    approved_final = final.isin(["invoiced", "processing"]) & h["approved_ts"].notna()
    status[approved_final] = final[approved_final]
    status[h["carrier_handover_ts"].notna()] = "shipped"
    status[h["delivered_ts"].notna()] = "delivered"
    terminal = final.isin(["canceled", "unavailable"])
    status[terminal] = final[terminal]
    h["order_status"] = status
    h["changed_at"] = h[MILESTONES].max(axis=1)
    return h


def tick_rows(tables: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """SQL rows to MERGE for the virtual window [start, end)."""
    win = order_window(tables, start, end)  # orders purchased in the window + their parents
    soh = tables["sales_order_header"]
    touched = pd.Series(False, index=soh.index)
    for c in MILESTONES:
        touched |= (soh[c] >= start) & (soh[c] < end)
    changed = soh[touched & (soh["purchase_ts"] < end)]
    win["sales_order_header"] = header_snapshot(changed, end - pd.Timedelta(microseconds=1))
    rev = tables["review"]
    win["review"] = rev[(rev["created_ts"] >= start) & (rev["created_ts"] < end) & rev["order_id"].isin(set(soh[soh["purchase_ts"] < end]["order_id"]))]
    return win


def tick_events(all_events, start: pd.Timestamp, end: pd.Timestamp):
    return [e for e in all_events if start <= e.ts < end]


def load_checkpoint(agent: str) -> pd.Timestamp | None:
    p = STATE / f"{agent}.json"
    return pd.Timestamp(json.loads(p.read_text())["virtual_time"]) if p.exists() else None


def save_checkpoint(agent: str, vt: pd.Timestamp, stats: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{agent}.json").write_text(json.dumps({"virtual_time": vt.isoformat(), "saved_at": dt.datetime.now(dt.UTC).isoformat(),
                                                     "last_tick": stats}, indent=1))


def run(tables: dict[str, pd.DataFrame], sql_sink, event_sink, agent: str, start: pd.Timestamp, speed: float,
        minutes: float, tick_s: float = 20, log=print) -> dict:
    """Run the virtual clock until --minutes elapse or a sink fails. Returns a summary; the checkpoint only
    ever points at the end of the last fully delivered window."""
    all_events = window_events({k: tables[k] for k in tables})
    vt = start
    wall0 = time.monotonic()
    totals = {"ticks": 0, "sql_rows": 0, "events": 0}
    while time.monotonic() - wall0 < minutes * 60:
        time.sleep(tick_s)
        end = start + pd.Timedelta(seconds=(time.monotonic() - wall0) * speed)
        rows, evs = tick_rows(tables, vt, end), tick_events(all_events, vt, end)
        try:
            merged = sql_sink.write_all(rows)
            sent = event_sink.send(evs)
        except Exception as e:  # noqa: BLE001 - capacity paused, network, ...: stop, keep the checkpoint
            log(f"STOPPED at virtual {vt} (window not delivered: {type(e).__name__}: {str(e)[:200]})")
            return {**totals, "stopped": True, "checkpoint": str(vt)}
        n_sql = sum(sum(v.values()) for v in merged.values())
        save_checkpoint(agent, end, {"window": [str(vt), str(end)], "sql_changes": n_sql, "events": sent})
        totals["ticks"] += 1
        totals["sql_rows"] += n_sql
        totals["events"] += sum(sent.values())
        log(f"tick {totals['ticks']:3d}  virtual {vt:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M}  sql changes {n_sql:4d}  events {sent}")
        vt = end
    return {**totals, "stopped": False, "checkpoint": str(vt)}
