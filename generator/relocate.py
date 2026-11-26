"""Relocate Olist to a German online retailer and write the shop tables as parquet.

    python -m generator.relocate --sample 0.05            # 5% of orders (Dev / feature)
    python -m generator.relocate --sample 1.0 --out DIR   # full set (Test / Prod)

Output (generator/data/out/<sample>/): one parquet per table in sql/01_shop_oltp.sql, with
exactly the columns of the Bronze shortcut contracts (contracts/bronze/shop_*.yml), plus the
reference files ref_geography_de and ref_category_translation.

Rules (SPEC §2):
- Each Olist zip prefix maps to a population-weighted German city + synthetic PLZ (geography.py).
- BRL -> EUR at a fixed rate; Olist prices are treated as gross. Net = gross / (1 + VAT).
- VAT 7% for books and food categories, 19% otherwise.
- Warehouse by Bundesland: north/east-north -> HAM, west -> DOR, south/east-south -> MUC.
- Timestamps shift by whole weeks so the latest purchase lands on the shift date (weekday patterns survive).
- Sampling keeps whole orders (hash of order_id), plus every customer, product and seller they reference.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib

import pandas as pd

from generator import OUT, RAW
from generator.download import download, is_downloaded
from generator.geography import (
    CITIES,
    REGIONS,
    WAREHOUSE_SITES,
    place_for,
    warehouse_for,
)
from generator.holidays import holiday_rows

BRL_TO_EUR = 0.25
REDUCED_VAT_CATEGORIES = {"livros_interesse_geral", "livros_tecnicos", "livros_importados", "alimentos", "alimentos_bebidas"}
PAYMENT_TYPE = {"credit_card": "credit_card", "boleto": "klarna_invoice", "voucher": "voucher", "debit_card": "sepa_debit", "not_defined": "credit_card"}
TS_COLS = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"]

# Column order = contracts/bronze/shop_*.yml = sql/01_shop_oltp.sql (tested in tests/pipelines).
COLUMNS = {
    "customer": ["customer_id", "customer_unique_id", "postal_code", "city", "region_code", "created_at", "changed_at"],
    "material": ["material_id", "category_code", "weight_g", "length_cm", "height_cm", "width_cm", "photos_qty", "vat_rate", "changed_at"],
    "vendor": ["vendor_id", "postal_code", "city", "region_code", "changed_at"],
    "sales_order_header": ["order_id", "customer_id", "order_status", "purchase_ts", "approved_ts", "carrier_handover_ts", "delivered_ts",
                           "estimated_delivery_date", "warehouse_code", "currency", "changed_at"],
    "sales_order_item": ["order_id", "item_no", "material_id", "vendor_id", "shipping_limit_ts", "net_price", "freight", "vat_rate", "changed_at"],
    "payment": ["order_id", "payment_seq", "payment_type", "installments", "amount", "changed_at"],
    "review": ["review_id", "order_id", "score", "title", "message", "created_ts", "answered_ts", "changed_at"],
}


def in_sample(key: str, fraction: float) -> bool:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 10_000 < round(fraction * 10_000)


def vat_rate(category: str | None) -> float:
    return 0.07 if category in REDUCED_VAT_CATEGORIES else 0.19


def eur(brl: pd.Series) -> pd.Series:
    return (brl.astype(float) * BRL_TO_EUR).round(2)


def week_shift(latest: pd.Timestamp, target: dt.date) -> pd.Timedelta:
    days = (pd.Timestamp(target) - latest.normalize()).days
    return pd.Timedelta(days=days - days % 7)


def read_raw(raw: pathlib.Path = RAW) -> dict[str, pd.DataFrame]:
    r = lambda n: pd.read_csv(raw / f"{n}.csv", dtype={"customer_zip_code_prefix": str, "seller_zip_code_prefix": str})
    return {
        "customers": r("olist_customers_dataset"), "orders": r("olist_orders_dataset"), "items": r("olist_order_items_dataset"),
        "payments": r("olist_order_payments_dataset"), "reviews": r("olist_order_reviews_dataset"), "products": r("olist_products_dataset"),
        "sellers": r("olist_sellers_dataset"), "categories": r("product_category_name_translation"),
    }


def relocate(src: dict[str, pd.DataFrame], sample: float = 1.0, target: dt.date | None = None) -> dict[str, pd.DataFrame]:
    target = target or dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    orders = src["orders"][src["orders"]["order_id"].map(lambda k: in_sample(k, sample))].copy()
    for c in TS_COLS:
        orders[c] = pd.to_datetime(orders[c])
    shift = week_shift(orders["order_purchase_timestamp"].max(), target)
    for c in TS_COLS:
        orders[c] = orders[c] + shift
    ids = set(orders["order_id"])

    items = src["items"][src["items"]["order_id"].isin(ids)].copy()
    items["shipping_limit_date"] = pd.to_datetime(items["shipping_limit_date"]) + shift
    payments = src["payments"][src["payments"]["order_id"].isin(ids)].copy()
    reviews = src["reviews"][src["reviews"]["order_id"].isin(ids)].drop_duplicates(["review_id", "order_id"]).copy()
    for c in ["review_creation_date", "review_answer_timestamp"]:
        reviews[c] = pd.to_datetime(reviews[c]) + shift
    customers = src["customers"][src["customers"]["customer_id"].isin(set(orders["customer_id"]))].copy()
    products = src["products"][src["products"]["product_id"].isin(set(items["product_id"]))].copy()
    sellers = src["sellers"][src["sellers"]["seller_id"].isin(set(items["seller_id"]))].copy()

    # --- geography
    cust_place = customers["customer_zip_code_prefix"].fillna("unknown").map(lambda z: place_for(f"c{z}"))
    customers = customers.assign(postal_code=cust_place.map(lambda p: p["postal_code"]), city=cust_place.map(lambda p: p["city"]),
                                 region_code=cust_place.map(lambda p: p["region_code"]))
    first_order = orders.groupby("customer_id")["order_purchase_timestamp"].min()
    customers["created_at"] = customers["customer_id"].map(first_order)
    customers["changed_at"] = customers["created_at"]
    sell_place = sellers["seller_zip_code_prefix"].fillna("unknown").map(lambda z: place_for(f"s{z}"))
    sellers = sellers.assign(vendor_id=sellers["seller_id"], postal_code=sell_place.map(lambda p: p["postal_code"]),
                             city=sell_place.map(lambda p: p["city"]), region_code=sell_place.map(lambda p: p["region_code"]))
    base_ts = orders["order_purchase_timestamp"].min()
    sellers["changed_at"] = base_ts

    # --- orders
    region_by_customer = customers.set_index("customer_id")["region_code"]
    soh = pd.DataFrame({
        "order_id": orders["order_id"], "customer_id": orders["customer_id"], "order_status": orders["order_status"],
        "purchase_ts": orders["order_purchase_timestamp"], "approved_ts": orders["order_approved_at"],
        "carrier_handover_ts": orders["order_delivered_carrier_date"], "delivered_ts": orders["order_delivered_customer_date"],
        "estimated_delivery_date": orders["order_estimated_delivery_date"].dt.date,
        "warehouse_code": orders["customer_id"].map(region_by_customer).map(warehouse_for), "currency": "EUR",
    })
    soh["changed_at"] = soh[["purchase_ts", "approved_ts", "carrier_handover_ts", "delivered_ts"]].max(axis=1)

    # --- products
    products["vat_rate"] = products["product_category_name"].map(vat_rate)
    material = pd.DataFrame({
        "material_id": products["product_id"], "category_code": products["product_category_name"],
        "weight_g": products["product_weight_g"], "length_cm": products["product_length_cm"], "height_cm": products["product_height_cm"],
        "width_cm": products["product_width_cm"], "photos_qty": products["product_photos_qty"], "vat_rate": products["vat_rate"], "changed_at": base_ts,
    })
    for c in ["weight_g", "length_cm", "height_cm", "width_cm", "photos_qty"]:
        material[c] = material[c].astype("Int64")

    # --- order items: Olist price is gross BRL
    vat = items["product_id"].map(products.set_index("product_id")["vat_rate"]).fillna(0.19)
    gross = eur(items["price"])
    soi = pd.DataFrame({
        "order_id": items["order_id"], "item_no": items["order_item_id"].astype(int), "material_id": items["product_id"],
        "vendor_id": items["seller_id"], "shipping_limit_ts": items["shipping_limit_date"], "net_price": (gross / (1 + vat)).round(2),
        "freight": eur(items["freight_value"]), "vat_rate": vat, "changed_at": items["shipping_limit_date"],
    })

    pay = pd.DataFrame({
        "order_id": payments["order_id"], "payment_seq": payments["payment_sequential"].astype(int),
        "payment_type": payments["payment_type"].map(PAYMENT_TYPE).fillna("credit_card"),
        "installments": payments["payment_installments"].astype(int), "amount": eur(payments["payment_value"]),
    })
    pay["changed_at"] = pay["order_id"].map(soh.set_index("order_id")["purchase_ts"])

    rev = pd.DataFrame({
        "review_id": reviews["review_id"], "order_id": reviews["order_id"], "score": reviews["review_score"].astype(int),
        "title": reviews["review_comment_title"], "message": reviews["review_comment_message"],
        "created_ts": reviews["review_creation_date"], "answered_ts": reviews["review_answer_timestamp"],
    })
    rev["changed_at"] = rev["answered_ts"].fillna(rev["created_ts"])

    out = {
        "customer": customers, "material": material, "vendor": sellers, "sales_order_header": soh,
        "sales_order_item": soi, "payment": pay, "review": rev,
    }
    out = {k: v[COLUMNS[k]].reset_index(drop=True) for k, v in out.items()}

    # --- reference data
    places = pd.concat([out["customer"][["postal_code", "city", "region_code"]], out["vendor"][["postal_code", "city", "region_code"]]])
    coords = {c[0]: (c[5], c[6]) for c in CITIES}
    geo = places.drop_duplicates("postal_code").copy()
    geo["lat"] = geo["city"].map(lambda c: coords[c][0])
    geo["lon"] = geo["city"].map(lambda c: coords[c][1])
    wh = pd.DataFrame([{"postal_code": p, "city": c, "region_code": r, "lat": la, "lon": lo} for p, c, r, la, lo in WAREHOUSE_SITES])
    geo = pd.concat([geo, wh]).drop_duplicates("postal_code")
    geo.insert(3, "region_name", geo["region_code"].map(lambda r: REGIONS[r][0]))
    out["ref_geography_de"] = geo.sort_values("postal_code").reset_index(drop=True)
    cat = src["categories"].rename(columns={"product_category_name": "category_code", "product_category_name_english": "category_en"})
    cat["category_de"] = None
    out["ref_category_translation"] = cat[["category_code", "category_en", "category_de"]]
    out["ref_holiday_de"] = pd.DataFrame(holiday_rows())
    return out


def write(tables: dict[str, pd.DataFrame], folder: pathlib.Path, meta: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(folder / f"{name}.parquet", index=False)
    (folder / "_manifest.json").write_text(json.dumps({**meta, "rows": {k: len(v) for k, v in tables.items()}}, indent=1, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=float, default=0.05, help="share of orders, 0 < x <= 1")
    ap.add_argument("--target-date", type=dt.date.fromisoformat, help="latest purchase date after the shift (default yesterday, UTC)")
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args()
    if not 0 < a.sample <= 1:
        ap.error("--sample must be in (0, 1]")
    if not is_downloaded():
        download()
    tables = relocate(read_raw(), a.sample, a.target_date)
    folder = a.out or OUT / f"sample_{a.sample:g}"
    write(tables, folder, {"sample": a.sample, "brl_to_eur": BRL_TO_EUR, "generated_at": dt.datetime.now(dt.UTC).isoformat()})
    print(f"wrote {folder}")
    for k, v in tables.items():
        print(f"  {k:26} {len(v):>7} rows")


if __name__ == "__main__":
    main()
