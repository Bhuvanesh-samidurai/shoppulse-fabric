# generator/ (owner: DE1)

Turns the public Olist dataset into a German online retailer and replays it as live data.

**Data honesty:** the README of the repo says it plainly: real marketplace data, relocated.
- Orders, items, payments and reviews are real Olist records. Prices are converted BRL→EUR.
- **German geography is synthetic.** Each Olist zip prefix maps to a real German city, weighted by population. The postcode is a 5-digit PLZ inside that city's range, but not guaranteed to exist.
- **Warehouse and carrier events are simulated** from each order's real timestamps.

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r generator\requirements.txt
.\.venv\Scripts\python.exe -m generator.download                 # Olist into generator/data/raw (Kaggle key from infra/.env)
.\.venv\Scripts\python.exe -m generator.relocate --sample 0.05   # Dev/feature set -> generator/data/out/sample_0.05/
.\.venv\Scripts\python.exe -m generator.relocate --sample 1.0    # Test/Prod set
```

`generator/data/` is git-ignored. Data never goes into the repo.

## Relocation rules (`relocate.py`, `geography.py`)

| Olist | ShopPulse | Rule |
|---|---|---|
| `customer_zip_code_prefix` | `postal_code`, `city`, `region_code` | Deterministic city per prefix, weighted by population; PLZ in the city's range |
| `seller_zip_code_prefix` | Vendor location | Same |
| `price`, `freight_value`, `payment_value` (BRL, gross) | EUR | × 0.25 |
| — | `vat_rate` | 7% for books and food categories, 19% otherwise |
| `price` (gross) | `net_price` | gross ÷ (1 + VAT) |
| `payment_type` | `payment_type` | boleto→klarna_invoice, debit_card→sepa_debit, others kept |
| Customer Bundesland | `warehouse_code` | HAM: SH, HH, HB, NI, MV, BE, BB. DOR: NW, HE, RP, SL. MUC: BW, BY, SN, ST, TH |
| All timestamps | Shifted | By whole weeks, so the latest purchase is yesterday and weekday patterns survive |
| `--sample x` | Subset | Whole orders by hash of `order_id`, plus every customer, product and seller they reference (foreign keys hold) |

**Output:** one parquet file per shop table, with the exact columns of `contracts/bronze/shop_*.yml` (tested), plus `ref_geography_de` and `ref_category_translation`. Each run also writes `_manifest.json` with row counts.

## Replayer design (`replay.py`; sinks are built in the next issue)

```
relocated tables ──► virtual clock (start, speed) ──► SqlSink        ──► sqldb_shop ──mirror──► Bronze shop_*
                                    │                                                            (CDC, seconds)
                                    └─ order_events() ──► EventstreamSink ──► es_wms / es_carrier ──► bronze.*_event
                                                                                                   └► Eventhouse
```

**Order lifecycle** (`order_events()`, deterministic per order):

| Step | Timestamp |
|---|---|
| PICK (WMS) | approved + 1–6 h, before handover |
| PACK (WMS) | + 10–60 min |
| LABEL_CREATED (carrier) | handover − 5 min |
| SHIP (WMS) + PICKED_UP (carrier) | `carrier_handover_ts` |
| IN_TRANSIT | + 4–12 h |
| OUT_FOR_DELIVERY | delivered − 2–6 h |
| DELIVERED | `delivered_ts` |

- **Late deliveries** (delivered after the promised date) carry a `delay_reason`.
- **Returns:** 40% of 1-star orders get a `RETURN_RECEIPT` 5–14 days after delivery.
- **Carrier choice:** per macro region (DHL, DPD, Hermes weights).

**Replay behaviour:**
- **Rate control:** a virtual clock advances `speed` × real time. Each tick emits the SQL rows and events with timestamps ≤ the clock. Dev runs 5% at 60× (one Olist day every 24 minutes).
- **Idempotency:**
  - `event_id = uuid5(order_id|item|type)`.
  - SQL writes are MERGE on the primary key.
  - A restart or a replayed window creates no duplicates, and Silver dedups on `event_id`.
- **Configuration:** connection strings come from `infra/.env` (`SQLDB_FEAT_CONNECTION_STRING`, `EVENTSTREAM_FEAT_*_CONNECTION_STRING`). The environment comes from `--env`.
- **F2 rule:** only one environment gets live data at a time, so stop the replayer when you're not working.

**Still open for the replayer issue:**
- Stock replenishment (`GOODS_RECEIPT`) so stock levels don't go negative.
- Whether the replayer runs on the laptop or the VPS.
