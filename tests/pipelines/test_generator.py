"""Unit tests for generator/ (owner: DE1). No Olist download needed: a tiny Olist-shaped fixture."""
from __future__ import annotations

import collections
import datetime as dt
import pathlib

import pytest
import yaml

pd = pytest.importorskip("pandas", reason="pip install -r generator/requirements.txt")
pytest.importorskip("pyarrow")

from generator import geography as geo
from generator.relocate import COLUMNS, in_sample, relocate, vat_rate, week_shift
from generator.replay import carrier_for, order_events

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = dt.date(2026, 11, 23)


def contract_columns(name: str) -> list[str]:
    d = yaml.safe_load((ROOT / "contracts" / "bronze" / f"{name}.yml").read_text(encoding="utf-8"))
    return [c["name"] for c in d["columns"]]


def olist(n_orders: int = 40) -> dict[str, pd.DataFrame]:
    orders, items, pays, revs, custs = [], [], [], [], []
    statuses = ["delivered"] * 6 + ["shipped", "canceled", "invoiced", "unavailable"]
    for i in range(n_orders):
        oid, cid = f"o{i:04d}", f"c{i:04d}"
        p = pd.Timestamp("2018-03-01 10:00") + pd.Timedelta(days=i)
        st = statuses[i % len(statuses)]
        delivered = p + pd.Timedelta(days=12 if i % 5 == 0 else 6) if st == "delivered" else pd.NaT
        orders.append({"order_id": oid, "customer_id": cid, "order_status": st, "order_purchase_timestamp": p,
                       "order_approved_at": p + pd.Timedelta(hours=1) if st != "canceled" else pd.NaT,
                       "order_delivered_carrier_date": p + pd.Timedelta(days=2) if st in ("delivered", "shipped") else pd.NaT,
                       "order_delivered_customer_date": delivered, "order_estimated_delivery_date": p + pd.Timedelta(days=10)})
        custs.append({"customer_id": cid, "customer_unique_id": f"u{i % 30:04d}", "customer_zip_code_prefix": f"{1000 + i % 7:05d}",
                      "customer_city": "sao paulo", "customer_state": "SP"})
        for j in range(1 + i % 2):
            items.append({"order_id": oid, "order_item_id": j + 1, "product_id": f"p{(i + j) % 5}", "seller_id": f"s{i % 3}",
                          "shipping_limit_date": p + pd.Timedelta(days=3), "price": 107.0, "freight_value": 20.0})
        pays.append({"order_id": oid, "payment_sequential": 1, "payment_type": ["credit_card", "boleto", "debit_card"][i % 3],
                     "payment_installments": 1, "payment_value": 127.0})
        revs.append({"review_id": f"r{i}", "order_id": oid, "review_score": 1 if i % 4 == 0 else 5, "review_comment_title": None,
                     "review_comment_message": "muito bom", "review_creation_date": p + pd.Timedelta(days=13),
                     "review_answer_timestamp": p + pd.Timedelta(days=14)})
    revs.append(dict(revs[0]))  # Olist has duplicate review rows
    cats = ["livros_tecnicos", "alimentos", "cama_mesa_banho", "informatica_acessorios", None]
    products = pd.DataFrame([{"product_id": f"p{k}", "product_category_name": cats[k], "product_name_lenght": 10, "product_description_lenght": 100,
                              "product_photos_qty": 1, "product_weight_g": 500, "product_length_cm": 20, "product_height_cm": 10,
                              "product_width_cm": 15} for k in range(5)])
    sellers = pd.DataFrame([{"seller_id": f"s{k}", "seller_zip_code_prefix": f"{2000 + k:05d}", "seller_city": "x", "seller_state": "SP"} for k in range(3)])
    cats_df = pd.DataFrame({"product_category_name": [c for c in cats if c], "product_category_name_english": ["books_technical", "food", "bed_bath_table", "computers_accessories"]})
    return {"customers": pd.DataFrame(custs), "orders": pd.DataFrame(orders), "items": pd.DataFrame(items), "payments": pd.DataFrame(pays),
            "reviews": pd.DataFrame(revs), "products": products, "sellers": sellers, "categories": cats_df}


@pytest.fixture(scope="module")
def full():
    return relocate(olist(), 1.0, TARGET)


# ------------------------------------------------------------------ geography
def test_city_mapping_is_deterministic_and_weighted():
    assert geo.place_for("c01310") == geo.place_for("c01310")
    counts = collections.Counter(geo.city_for(f"k{i}")[0] for i in range(20_000))
    assert counts.most_common(1)[0][0] == "Berlin"
    assert counts["Berlin"] > counts["Flensburg"] * 10


def test_postcodes_are_valid_and_inside_city_range():
    ranges = {c[0]: (c[2], c[3]) for c in geo.CITIES}
    for i in range(2_000):
        p = geo.place_for(f"z{i}")
        assert len(p["postal_code"]) == 5 and p["postal_code"].isdigit()
        lo, hi = ranges[p["city"]]
        assert lo <= int(p["postal_code"]) <= hi
        assert p["region_code"] in geo.REGIONS


def test_every_bundesland_has_a_city_and_a_warehouse():
    assert {c[1] for c in geo.CITIES} == set(geo.REGIONS)
    assert {geo.warehouse_for(r) for r in geo.REGIONS} == {"HAM", "DOR", "MUC"}


# ------------------------------------------------------------------ relocation
@pytest.mark.parametrize("table", list(COLUMNS))
def test_output_columns_equal_bronze_contracts(full, table):
    assert list(full[table].columns) == contract_columns(f"shop_{table}")


def test_foreign_keys_hold(full):
    assert set(full["sales_order_header"]["customer_id"]) <= set(full["customer"]["customer_id"])
    assert set(full["sales_order_item"]["order_id"]) <= set(full["sales_order_header"]["order_id"])
    assert set(full["sales_order_item"]["material_id"]) <= set(full["material"]["material_id"])
    assert set(full["sales_order_item"]["vendor_id"]) <= set(full["vendor"]["vendor_id"])
    assert set(full["payment"]["order_id"]) <= set(full["sales_order_header"]["order_id"])
    geo_plz = set(full["ref_geography_de"]["postal_code"])
    assert set(full["customer"]["postal_code"]) | set(full["vendor"]["postal_code"]) <= geo_plz
    assert {"21129", "44147", "85748"} <= geo_plz  # warehouse sites


def test_primary_keys_unique(full):
    for table, pk in [("customer", ["customer_id"]), ("sales_order_header", ["order_id"]), ("sales_order_item", ["order_id", "item_no"]),
                      ("payment", ["order_id", "payment_seq"]), ("review", ["review_id", "order_id"]), ("ref_geography_de", ["postal_code"])]:
        assert not full[table].duplicated(pk).any(), table


def test_eur_and_vat():
    assert vat_rate("livros_tecnicos") == 0.07 and vat_rate("alimentos") == 0.07
    assert vat_rate("cama_mesa_banho") == 0.19 and vat_rate(None) == 0.19
    t = relocate(olist(), 1.0, TARGET)
    soi = t["sales_order_item"]
    gross = (soi["net_price"] * (1 + soi["vat_rate"])).round(2)
    assert ((gross - 107.0 * 0.25).abs() <= 0.01).all()  # 107 BRL gross -> 26.75 EUR gross
    assert (soi["freight"] == 5.0).all()
    assert set(t["sales_order_header"]["currency"]) == {"EUR"}


def test_warehouse_follows_customer_bundesland(full):
    region = full["customer"].set_index("customer_id")["region_code"]
    soh = full["sales_order_header"]
    assert (soh["warehouse_code"] == soh["customer_id"].map(region).map(geo.warehouse_for)).all()


def test_timestamps_shift_by_whole_weeks_to_target(full):
    soh = full["sales_order_header"]
    assert soh["purchase_ts"].max().date() <= TARGET
    assert (TARGET - soh["purchase_ts"].max().date()).days < 7
    original = pd.Timestamp("2018-03-01 10:00")
    assert (soh["purchase_ts"].min() - original).days % 7 == 0
    assert week_shift(pd.Timestamp("2018-03-01 10:00"), dt.date(2018, 3, 15)) == pd.Timedelta(days=14)


def test_payment_types_mapped(full):
    assert set(full["payment"]["payment_type"]) <= {"credit_card", "klarna_invoice", "sepa_debit", "voucher"}


def test_sample_is_deterministic_subset():
    a, b = relocate(olist(200), 0.3, TARGET), relocate(olist(200), 0.3, TARGET)
    assert a["sales_order_header"]["order_id"].tolist() == b["sales_order_header"]["order_id"].tolist()
    assert 30 < len(a["sales_order_header"]) < 90
    assert in_sample("anything", 1.0)


# ------------------------------------------------------------------ replay events
def _order(full, status="delivered"):
    soh = full["sales_order_header"]
    o = soh[soh["order_status"] == status].iloc[0]
    return o, full["sales_order_item"][full["sales_order_item"]["order_id"] == o["order_id"]]


def test_events_are_deterministic_ordered_and_unique(full):
    o, items = _order(full)
    e1, e2 = order_events(o, items, "10115", "Ost", 5), order_events(o, items, "10115", "Ost", 5)
    assert [e.payload for e in e1] == [e.payload for e in e2]
    assert [e.ts for e in e1] == sorted(e.ts for e in e1)
    assert len({e.payload["event_id"] for e in e1}) == len(e1)
    kinds = [e.payload.get("event_type") or e.payload.get("status") for e in e1]
    assert kinds.count("PICK") == len(items) and "DELIVERED" in kinds
    assert all(e.payload["event_ts"] >= o["approved_ts"] for e in e1 if e.stream == "wms")


def test_cancelled_orders_have_no_events(full):
    o, items = _order(full, "canceled")
    assert order_events(o, items, "10115", "Ost") == []


def test_late_delivery_has_delay_reason(full):
    soh = full["sales_order_header"]
    late = soh[(soh["order_status"] == "delivered") & (soh["delivered_ts"].dt.normalize() > pd.to_datetime(soh["estimated_delivery_date"]))].iloc[0]
    items = full["sales_order_item"][full["sales_order_item"]["order_id"] == late["order_id"]]
    delivered = [e for e in order_events(late, items, "10115", "Ost") if e.payload.get("status") == "DELIVERED"]
    assert delivered and delivered[0].payload["delay_reason"]


def test_event_payloads_match_bronze_contracts(full):
    meta = {"_ingested_at", "_source", "_batch_id"}
    o, items = _order(full)
    events = order_events(o, items, "10115", "Ost", 1)
    for stream, contract in [("wms", "wms_event"), ("carrier", "carrier_event")]:
        want = [c for c in contract_columns(contract) if c not in meta]
        for e in (e for e in events if e.stream == stream):
            assert sorted(e.payload) == sorted(want), contract


def test_carrier_choice_is_stable():
    assert carrier_for("o1", "Nord") == carrier_for("o1", "Nord")
    assert {carrier_for(f"o{i}", "West") for i in range(200)} == {"DHL", "DPD", "HERMES"}


# ------------------------------------------------------------------ reference data (#25)
def test_easter_and_national_holidays():
    from generator.holidays import easter, holiday_rows, national
    assert easter(2025) == dt.date(2025, 4, 20) and easter(2026) == dt.date(2026, 4, 5)
    assert len(national(2026)) == 9
    rows = holiday_rows(2026, 2026)
    assert {r["scope"] for r in rows} <= set(geo.REGIONS) | {"DE"}
    assert {"holiday_date": dt.date(2026, 10, 31), "holiday_name": "Reformationstag", "scope": "HH"} in rows
    assert len({(r["holiday_date"], r["scope"]) for r in rows}) == len(rows)  # PK unique


@pytest.mark.parametrize("ref", ["ref_geography_de", "ref_category_translation", "ref_holiday_de"])
def test_reference_outputs_match_bronze_contracts(full, ref):
    meta = {"_ingested_at", "_source", "_batch_id"}
    assert list(full[ref].columns) == [c for c in contract_columns(ref) if c not in meta]


# ------------------------------------------------------------------ SqlSink (#26)
def test_merge_sql_updates_only_changed_rows():
    from generator.sqlsink import KEYS, ORDER, merge_sql
    sql = merge_sql("sales_order_item")
    assert "ON t.[order_id] = s.[order_id] AND t.[item_no] = s.[item_no]" in sql
    assert "WHEN MATCHED AND EXISTS (SELECT" in sql and "EXCEPT SELECT" in sql  # no-op when nothing differs
    assert "WHEN NOT MATCHED BY TARGET THEN INSERT" in sql and "OUTPUT $action" in sql
    assert ORDER.index("customer") < ORDER.index("sales_order_header") < ORDER.index("sales_order_item")
    assert set(KEYS) == set(COLUMNS)


def test_rows_are_typed_for_sql(full):
    from decimal import Decimal

    from generator.sqlsink import SQL_TYPES, input_sizes, to_rows
    for table in COLUMNS:
        assert all(c in SQL_TYPES for c in COLUMNS[table])
        assert len(input_sizes(table)) == len(COLUMNS[table])
    soi = to_rows(full["sales_order_item"], "sales_order_item")
    assert isinstance(soi[0][COLUMNS["sales_order_item"].index("net_price")], Decimal)
    soh = to_rows(full["sales_order_header"], "sales_order_header")
    i = COLUMNS["sales_order_header"].index("delivered_ts")
    assert any(r[i] is None for r in soh)  # NaT -> NULL
    assert all(r[i] is None or r[i].microsecond % 1000 == 0 for r in soh)  # DATETIME2(3)


def test_order_window_keeps_foreign_keys(full):
    from generator.replay import order_window
    soh = full["sales_order_header"]
    start = soh["purchase_ts"].min()
    w = order_window(full, start, start + pd.Timedelta(days=10))
    assert 0 < len(w["sales_order_header"]) < len(soh)
    assert set(w["sales_order_header"]["customer_id"]) <= set(w["customer"]["customer_id"])
    assert set(w["sales_order_item"]["material_id"]) <= set(w["material"]["material_id"])
    assert set(w["sales_order_item"]["vendor_id"]) <= set(w["vendor"]["vendor_id"])
    assert set(w["review"]["order_id"]) <= set(w["sales_order_header"]["order_id"])


# ------------------------------------------------------------------ Eventstreams (#27)
import json as _json
import re as _re


def _platform(path):
    return _json.loads((ROOT / "fabric" / "data" / path / ".platform").read_text(encoding="utf-8"))


@pytest.mark.parametrize("es,table", [("es-wms", "wms_event"), ("es-carrier", "carrier_event")])
def test_eventstream_definitions_are_portable(es, table):
    topo = _json.loads((ROOT / "fabric" / "data" / f"{es}.Eventstream" / "eventstream.json").read_text(encoding="utf-8"))
    lh = _platform("lh_bronze.Lakehouse")["config"]["logicalId"]
    kql = _platform("kqldb_shoppulse.KQLDatabase")["config"]["logicalId"]
    names = [n["name"] for k in ("sources", "operators", "destinations") for n in topo[k]]
    assert all(_re.fullmatch(r"[A-Za-z0-9]{3,63}", n) for n in names), names  # Eventstream node-name rule
    assert [s["type"] for s in topo["streams"]] == ["DefaultStream"]
    dest = {d["type"]: d["properties"] for d in topo["destinations"]}
    zero = "00000000-0000-0000-0000-000000000000"
    assert dest["Lakehouse"]["workspaceId"] == zero and dest["Lakehouse"]["itemId"] == lh  # fabric-cicd rewrites per env
    assert dest["Lakehouse"]["deltaTable"] == f"landing_{table}"
    assert dest["Eventhouse"]["itemId"] == kql and dest["Eventhouse"]["databaseName"] == kql  # engine DB name = KQL item id
    assert dest["Eventhouse"]["tableName"] == table and dest["Eventhouse"]["dataIngestionMode"] == "ProcessedIngestion"


def test_kql_schema_matches_event_contracts():
    kql = (ROOT / "fabric" / "data" / "kqldb_shoppulse.KQLDatabase" / "DatabaseSchema.kql").read_text(encoding="utf-8")
    meta = {"_ingested_at", "_source", "_batch_id"}
    for table in ("wms_event", "carrier_event"):
        cols = _re.search(rf"\.create-merge table {table} \((.*?)\)", kql).group(1)
        assert [c.split(":")[0].strip() for c in cols.split(",")] == [c for c in contract_columns(table) if c not in meta]


def test_event_payloads_encode_to_json(full):
    from generator.eventsink import encode
    from generator.replay import window_events
    soh = full["sales_order_header"]
    w = __import__("generator.replay", fromlist=["order_window"]).order_window(full, soh["purchase_ts"].min(), soh["purchase_ts"].max() + pd.Timedelta(days=1))
    evs = window_events(w)
    assert evs
    d = _json.loads(encode(evs[0].payload))
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["event_date"]) and "T" in d["event_ts"]


# ------------------------------------------------------------------ live replay (#28)
def test_header_snapshot_hides_future_milestones(full):
    from generator.live import header_snapshot
    soh = full["sales_order_header"]
    o = soh[(soh["order_status"] == "delivered")].iloc[[0]]
    before_ship = o["carrier_handover_ts"].iloc[0] - pd.Timedelta(minutes=1)
    s = header_snapshot(o, before_ship).iloc[0]
    assert s["order_status"] == "approved" and pd.isna(s["carrier_handover_ts"]) and pd.isna(s["delivered_ts"])
    s = header_snapshot(o, o["delivered_ts"].iloc[0]).iloc[0]
    assert s["order_status"] == "delivered" and s["changed_at"] == o["delivered_ts"].iloc[0]
    c = soh[soh["order_status"] == "canceled"].iloc[[0]]
    assert header_snapshot(c, c["purchase_ts"].iloc[0]).iloc[0]["order_status"] == "canceled"


def test_ticks_partition_rows_and_events(full):
    from generator.live import tick_events, tick_rows
    from generator.replay import window_events
    soh = full["sales_order_header"]
    t0, t2 = soh["purchase_ts"].min(), soh["purchase_ts"].max() + pd.Timedelta(days=30)
    t1 = t0 + (t2 - t0) / 2
    a, b = tick_rows(full, t0, t1), tick_rows(full, t1, t2)
    assert set(a["sales_order_item"]["order_id"]).isdisjoint(b["sales_order_item"]["order_id"])  # each order's items sent once
    assert len(a["sales_order_item"]) + len(b["sales_order_item"]) == len(full["sales_order_item"])
    evs = window_events(full)
    assert len(tick_events(evs, t0, t1)) + len(tick_events(evs, t1, t2)) == len([e for e in evs if t0 <= e.ts < t2])


def test_checkpoint_roundtrip(tmp_path, monkeypatch):
    from generator import live
    monkeypatch.setattr(live, "STATE", tmp_path)
    assert live.load_checkpoint("de1") is None
    live.save_checkpoint("de1", pd.Timestamp("2025-08-03 17:00"), {"events": {"wms": 1}})
    assert live.load_checkpoint("de1") == pd.Timestamp("2025-08-03 17:00")
