"""End-to-end count check for a virtual window (owner: DE1, issue #28).

    python -m generator.e2e --agent de1 --from 2025-08-01 --to 2025-08-08T12:00

Compares what the replayer should have delivered for [from, to) with what is actually in the agent's
feature workspace: sqldb_shop, the Bronze mirror shortcuts, the KQL hot path (distinct event_id) and the
Bronze contract event tables (after nb_ingest_events). Also checks that no order shows a milestone that
lies after `to` (progressive status). Exit code 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pandas as pd
import requests

from generator import OUT
from generator.replay import feature_sqldb, window_events

API = "https://api.fabric.microsoft.com/v1"


def expected(tables, start, end) -> dict[str, int]:
    soh = tables["sales_order_header"]
    ids = set(soh[(soh["purchase_ts"] >= start) & (soh["purchase_ts"] < end)]["order_id"])
    evs = [e for e in window_events(tables) if start <= e.ts < end]
    return {"orders": len(ids), "order_items": int(tables["sales_order_item"]["order_id"].isin(ids).sum()),
            "wms_events": sum(e.stream == "wms" for e in evs), "carrier_events": sum(e.stream == "carrier" for e in evs)}


def actual(agent, start, end) -> dict[str, int]:
    import struct

    import deltalake
    import pyodbc

    server, database, cred = feature_sqldb(agent)
    tok = cred.get_token("https://database.windows.net/.default").token.encode("utf-16-le")
    conn = pyodbc.connect(f"Driver={{ODBC Driver 18 for SQL Server}};Server={server};Database={database};Encrypt=yes",
                          attrs_before={1256: struct.pack(f"<I{len(tok)}s", len(tok), tok)})
    q = lambda sql: conn.cursor().execute(sql, start.to_pydatetime(), end.to_pydatetime()).fetchone()[0]
    out = {
        "orders": q("SELECT COUNT(*) FROM shop.sales_order_header WHERE purchase_ts >= ? AND purchase_ts < ?"),
        "order_items": q("SELECT COUNT(*) FROM shop.sales_order_item i JOIN shop.sales_order_header h ON h.order_id = i.order_id "
                         "WHERE h.purchase_ts >= ? AND h.purchase_ts < ?"),
        "future_milestones_in_sql": conn.cursor().execute(
            "SELECT COUNT(*) FROM shop.sales_order_header WHERE purchase_ts >= ? AND purchase_ts < ? AND "
            "(approved_ts >= ? OR carrier_handover_ts >= ? OR delivered_ts >= ?)",
            start.to_pydatetime(), end.to_pydatetime(), *[end.to_pydatetime()] * 3).fetchone()[0],
    }
    conn.close()

    h = {"Authorization": "Bearer " + cred.get_token("https://api.fabric.microsoft.com/.default").token}
    ws = next(w["id"] for w in requests.get(f"{API}/workspaces", headers=h, timeout=60).json()["value"] if w["displayName"] == f"shoppulse-feat-{agent}")
    lh = next(i["id"] for i in requests.get(f"{API}/workspaces/{ws}/items?type=Lakehouse", headers=h, timeout=60).json()["value"]
              if i["displayName"] == "lh_bronze")
    so = {"bearer_token": cred.get_token("https://storage.azure.com/.default").token, "use_fabric_endpoint": "true"}

    def bronze(table, ts_col, cols):
        df = deltalake.DeltaTable(f"abfss://{ws}@onelake.dfs.fabric.microsoft.com/{lh}/Tables/dbo/{table}", storage_options=so) \
            .to_pandas(engine="datafusion", columns=cols)
        ts = pd.to_datetime(df[ts_col], utc=True).dt.tz_localize(None)
        return df[(ts >= start) & (ts < end)]

    out["bronze_mirror_orders"] = len(bronze("shop_sales_order_header", "purchase_ts", ["order_id", "purchase_ts"]))
    out["bronze_wms_events"] = bronze("wms_event", "event_ts", ["event_id", "event_ts"])["event_id"].nunique()
    out["bronze_carrier_events"] = bronze("carrier_event", "event_ts", ["event_id", "event_ts"])["event_id"].nunique()

    db = next(d for d in requests.get(f"{API}/workspaces/{ws}/kqlDatabases", headers=h, timeout=60).json()["value"]
              if d["displayName"] == "kqldb_shoppulse")
    kt = cred.get_token("https://kusto.kusto.windows.net/.default").token
    for t, key in [("wms_event", "kql_wms_events"), ("carrier_event", "kql_carrier_events")]:
        csl = f"{t} | where event_ts >= datetime({start.isoformat()}) and event_ts < datetime({end.isoformat()}) | summarize dcount(event_id)"
        r = requests.post(f"{db['properties']['queryServiceUri']}/v1/rest/query", headers={"Authorization": f"Bearer {kt}"},
                          json={"db": db["id"], "csl": csl}, timeout=60)
        out[key] = r.json()["Tables"][0]["Rows"][0][0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--from", dest="start", required=True, type=pd.Timestamp)
    ap.add_argument("--to", dest="end", required=True, type=pd.Timestamp)
    ap.add_argument("--dir", type=pathlib.Path, default=OUT / "sample_0.05")
    a = ap.parse_args()
    tables = {n: pd.read_parquet(a.dir / f"{n}.parquet") for n in
              ["customer", "material", "vendor", "sales_order_header", "sales_order_item", "payment", "review"]}
    exp, act = expected(tables, a.start, a.end), actual(a.agent, a.start, a.end)
    checks = {
        "sql orders": (exp["orders"], act["orders"]),
        "sql order items": (exp["order_items"], act["order_items"]),
        "sql future milestones (must be 0)": (0, act["future_milestones_in_sql"]),
        "bronze mirror orders": (exp["orders"], act["bronze_mirror_orders"]),
        "kql wms events (distinct)": (exp["wms_events"], act["kql_wms_events"]),
        "kql carrier events (distinct)": (exp["carrier_events"], act["kql_carrier_events"]),
        "bronze wms_event": (exp["wms_events"], act["bronze_wms_events"]),
        "bronze carrier_event": (exp["carrier_events"], act["bronze_carrier_events"]),
    }
    for k, (e, v) in checks.items():
        print(f"{'OK ' if e == v else 'BAD'} {k:36} expected {e:6}  actual {v}")
    print(json.dumps({"window": [str(a.start), str(a.end)]}))
    return 0 if all(e == v for e, v in checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
