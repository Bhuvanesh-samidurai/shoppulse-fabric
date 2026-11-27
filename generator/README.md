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
.\.venv\Scripts\python.exe -m generator.onelake --agent de1 --what ref   # upload ref_* to the agent's lh_bronze Files/landing/ref
# then run nb_ingest_ref in that workspace -> bronze.ref_geography_de, ref_category_translation, ref_holiday_de
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

## SqlSink: shop rows into sqldb_shop (built, #26)

```powershell
.\.venv\Scripts\python.exe -m generator.replay sql-load --agent de1 --from 2025-02-01 --to 2025-08-01
```

**How it writes:**
- MERGEs the orders purchased in the window, plus every customer, product and seller they reference, into the agent's own `sqldb_shop`.
- Order is parent-first: customer, material, vendor, header, item, payment, review.
- Each table goes through a `#staging` table and a MERGE on the primary key. A matched row is updated only when a value differs.

**Behaviour:**
- **Idempotent:** re-sending a window gives `INSERT 0 / UPDATE 0`.
- **Partial failure:** a failed run can leave earlier tables committed. A re-run completes the rest.
- **Parameter types are declared explicitly.** Otherwise pyodbc's `fast_executemany` guesses VARCHAR for a numeric column whose first value is NULL.
- **Connection retry:** connect retries for up to 4 minutes while the database wakes after a capacity resume.

**Measured in `shoppulse-feat-de1` (2026-11-26):**
- 4,000 rows load in about 15 s.
- The SQL DB mirror shows them in the `bronze.shop_*` shortcuts within about 25 s.

### Identity for Dev/Test/Prod (decision needed)
Agents may only write to their own feature workspace (SPEC §4a). Live data for Dev, Test and Prod needs a writer.

**Proposal:** one service principal per environment, `sp-replayer-<env>`, with only:
- `db_datareader` + `db_datawriter` on that environment's `sqldb_shop` (no DDL, no workspace role beyond Viewer);
- the Eventstream custom-endpoint keys (#27).

The secrets stay with Bhuvanesh (or the VPS later), never with an agent.

## EventstreamSink: WMS and carrier events (built, #27)

```powershell
.\.venv\Scripts\python.exe -m generator.replay events --agent de1 --from 2024-12-01 --to 2025-02-01
```

**Data path:**

```
replayer ──► es-wms / es-carrier (custom endpoint)
               ├─► EventhouseHot  ─► kqldb_shoppulse.wms_event / carrier_event      (~30 s, hot path, 3-day retention)
               └─► BronzeLanding  ─► lh_bronze.landing_wms_event / landing_carrier_event   (flush every 2 min)
                                        └─ nb_ingest_events (micro-batch) ─► bronze.wms_event / bronze.carrier_event (contract)
```

**Why a landing step:** Eventstream's Lakehouse destination writes its own schema: strings for timestamps and dates, `long` for ints, plus `EventProcessedUtcTime`, `PartitionId` and `EventEnqueuedUtcTime`. It has no `date` type, so it can't write into the contract tables directly. `nb_ingest_events` handles the conversion:
- **Typing:** casts to the contract types.
- **Metadata:** sets `_ingested_at` = `EventEnqueuedUtcTime`.
- **Quarantine:** bad rows go to a quarantine table.
- **Dedupe:** MERGE on `event_id`.
- **Incremental:** it runs from the last watermark in `ops.run_log`, with a 10-minute overlap.

**Security:** the endpoint connection string is fetched at runtime with the agent's identity. It stays in memory only and is never printed or saved.

**Measured in `shoppulse-feat-de1` (2026-11-26), 174 events sent twice:**

| Where | Result |
|---|---|
| KQL | 81 / 93 rows within 45 s |
| Landing | 162 / 186 rows (both sends) |
| Bronze contract tables | 81 / 93 unique events |
| Second ingest run | inserted 0 |

## Live replay with checkpoint/resume (built, #28)

```powershell
.\.venv\Scripts\python.exe -m generator.replay run --agent de1 --start 2025-08-01 --speed 1440 --minutes 10
.\.venv\Scripts\python.exe -m generator.replay run --agent de1 --resume --speed 1440 --minutes 10
.\.venv\Scripts\python.exe -m generator.e2e --agent de1 --from 2025-08-01 --to <checkpoint>    # after nb_ingest_events
.\.venv\Scripts\python.exe -m generator.capacity status|pause|resume                            # as Bhuvanesh (cached sign-in)
```

**How it runs:**
- **Virtual clock:** the clock advances `--speed` × real time. Every tick (20 s by default), the shop rows and events whose business timestamp fell into that window are sent.
- **Progressive order status:** an order header is MERGEd again at each milestone. Later milestones stay NULL, and the status is whatever it was at that moment. The e2e check verifies that no order shows a future milestone.
- **Checkpoint:** stored in `generator/data/state/<agent>.json`. It moves only after both SQL and the events of a tick succeed. On any failure the run stops (exit code 2) and `--resume` replays the undelivered window. SQL is MERGE and Bronze dedups on `event_id`, so the replay is safe.

**Capacity pause, tested 2026-11-26 in `shoppulse-feat-de1`:**
1. The capacity was paused mid-run. The 8th tick's events were rejected ("CBS Token authentication failed"), and the replayer stopped at checkpoint virtual 2025-08-03 17:00.
2. After resume, `--resume` continued from exactly that point and ran 16 more ticks.
3. **Finding:** after a capacity resume, Eventstream *sources* are Running again but *destinations* stay **Paused**. Events are accepted and kept (1-day retention) but not delivered.
   - Resuming the destinations `WhenLastStopped` delivered everything.
   - `EventstreamSink` now does this automatically on start (`resume_paused_destinations`).
4. **End-to-end over virtual 1–10 Aug 2025:**

| Check | Result |
|---|---|
| SQL | 40 orders, 43 items |
| Future milestones | 0 |
| Bronze mirror | 40 orders |
| KQL distinct events | 158 WMS / 244 carrier |
| Bronze event tables | 158 / 244 |

All 8 checks OK.

**KQL hot path:** a replayed window can reach KQL twice. KQL queries and dashboards count `dcount(event_id)` or use `arg_max` per `event_id`.

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
