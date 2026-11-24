"""Download the Olist Brazilian e-commerce dataset from Kaggle into generator/data/raw.

    python -m generator.download [--force]

Credentials come from infra/.env: KAGGLE_API_TOKEN (new KGAT_ token), or KAGGLE_USERNAME + KAGGLE_KEY.
Uses the official kaggle package: the old REST path /api/v1/datasets/download returns 404 now.
"""
from __future__ import annotations

import os
import sys

from generator import RAW, load_env

DATASET = "olistbr/brazilian-ecommerce"
EXPECTED = [
    "olist_customers_dataset.csv", "olist_orders_dataset.csv", "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv", "olist_order_reviews_dataset.csv", "olist_products_dataset.csv",
    "olist_sellers_dataset.csv", "product_category_name_translation.csv",
]


def is_downloaded() -> bool:
    return all((RAW / f).exists() for f in EXPECTED)


def download(force: bool = False) -> None:
    if is_downloaded() and not force:
        print(f"Olist already in {RAW}")
        return
    env = load_env()
    for k in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        if env.get(k):
            os.environ[k] = env[k]  # process only; never written anywhere
    if not (env.get("KAGGLE_API_TOKEN") or (env.get("KAGGLE_USERNAME") and env.get("KAGGLE_KEY"))):
        sys.exit("No Kaggle credentials in infra/.env (KAGGLE_API_TOKEN or KAGGLE_USERNAME + KAGGLE_KEY)")
    from kaggle.api.kaggle_api_extended import KaggleApi  # imported late: it reads credentials on import/authenticate

    api = KaggleApi()
    api.authenticate()
    RAW.mkdir(parents=True, exist_ok=True)
    api.dataset_download_files(DATASET, path=str(RAW), unzip=True, quiet=True)
    missing = [f for f in EXPECTED if not (RAW / f).exists()]
    if missing:
        sys.exit(f"Download incomplete, missing: {missing}")
    print(f"Olist downloaded to {RAW}")


if __name__ == "__main__":
    download(force="--force" in sys.argv)
