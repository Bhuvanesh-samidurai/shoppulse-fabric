# ShopPulse SPEC

Source of truth for this repo. Every agent reads this before any task. If something here conflicts with an issue, stop and ask in the issue.

## 1. Goal

A real-time e-commerce fulfilment and supply chain platform on Microsoft Fabric for a fictional German online retailer. It covers the shop, three warehouses and the carriers, and it shows:

- **Descriptive:** what is happening now (orders, stock, deliveries).
- **Predictive:** late-delivery risk and demand forecasts.
- **Prescriptive:** reorder quantities, carrier choices, peak staffing.

Managers act on the data in a Fabric App with write-back.

Portfolio project. Built by five AI agents plus Bhuvanesh (product owner, Prod release manager).

## 2. Data

| Source system | Content | Ingestion | Latency |
|---|---|---|---|
| Shop OLTP (Fabric SQL Database) | Orders, items, payments, reviews, customers | Fabric SQL DB mirroring (CDC) to OneLake | Seconds |
| Warehouse WMS (events) | Goods receipt, stock movement, pick, pack, return across HAM, DOR, MUC warehouses | Eventstream | Sub-second |
| Carrier tracking (events) | DHL/DPD/Hermes-style status events | Eventstream | Sub-second |
| Reference data | Products, sellers, German geography (PLZ, Bundesland), holidays | Data Pipeline, one-time | Batch |

- **Base dataset:** Olist Brazilian e-commerce (Kaggle).
- **Relocation:** the `generator/` code maps it to German postcodes and Bundesländer, converts prices to EUR with 19% / 7% VAT, and applies German holidays and Black Friday / Q4 peaks.
- **Simulated events:** WMS and carrier events are generated from each order's real timestamps.
- **Disclosure:** the README must state this ("real marketplace data, relocated; WMS and carrier events simulated").
- **Replayer:** `generator/` replays orders as a live stream. Dev uses a 5% sample at 60× speed; Test and Prod use the full dataset.

## 3. Architecture

```
SQL DB (OLTP) ──mirroring──┐
WMS events ───Eventstream──┼──► BRONZE lakehouse (Delta, raw, append-only)
Carrier events Eventstream─┤          │ Spark Structured Streaming (1-min trigger)
                           │          ▼
                           │    SILVER lakehouse (clean, dedup, conformed, LLM enrichment)
                           │          │ MERGE / SCD2
                           │          ▼
                           │    GOLD lakehouse (Kimball star schema)
                           └──► Eventhouse (KQL) hot path, 7 days
                                      │
       Direct Lake semantic model  +  Real-Time Dashboard  +  Activator alerts
                      │
       Power BI report  →  Fabric App with write-back (User Data Function → SQL DB temporal tables)
```

- **Time travel:**
  - Delta `VERSION AS OF` / `TIMESTAMP AS OF` / `RESTORE` on every lakehouse table.
  - SQL temporal tables for write-back.
  - Eventhouse retention policies.
- **AI:**
  - Silver notebooks call the Nebius OpenAI-compatible API to translate review text, score sentiment and tag complaint topics. Fabric AI functions and Copilot aren't available on trial capacity.
  - Machine learning uses MLflow + LightGBM.
  - Anomaly detection uses KQL `series_decompose_anomalies`.
- **GDPR:**
  - Customer IDs are pseudonymised in Silver.
  - Deletion-request flow: delete, then `VACUUM` after retention.
  - RLS by Bundesland region.

### Gold model (v1, owned by DE2)

- **Facts:**
  - `fact_order_line`: grain is one order item.
  - `fact_payment`.
  - `fact_delivery`: accumulating snapshot through order, pick, ship, deliver.
  - `fact_stock_snapshot`: daily, per warehouse × product.
  - `fact_review`.
  - `fact_return`.
- **Dimensions:** `dim_customer` (SCD2), `dim_product` (SCD2), `dim_seller`, `dim_warehouse`, `dim_carrier`, `dim_geography`, `dim_date`, `dim_time`.
- **Data science outputs:** `pred_delivery_delay`, `pred_demand_forecast`, `rec_reorder`, `rec_carrier`.
- **Write-back tables (SQL DB):** `wb_reorder_policy`, `wb_forecast_override`, `wb_delivery_flag`.

### KPIs

- **OTIF:** on time in full.
- **Return rate.**
- **Stock coverage days.**
- **Carrier delay** by Bundesland.
- **Pick-to-ship hours.**
- **Review sentiment.**
- **GMV** in EUR (net and gross of VAT).

## 4. Environments

| Env | Fabric workspace | Branch | Deployed by | Data |
|---|---|---|---|---|
| Feature | `shoppulse-feat-<agent>` | `<agent>/<issue>-<name>` | The agent | 5% sample |
| Dev | `shoppulse-dev` | `dev` | CI on merge | 5% sample |
| Test | `shoppulse-test` | `main` | CI on merge | Full replay |
| Prod | `shoppulse-prod` | `main` (approved run) | **Bhuvanesh only** (GitHub Environment `prod`) | Full replay |

- **Capacity:** all workspaces sit on one Fabric Trial capacity (60 days). Record the demo before it expires.
- **Per-environment IDs:** connection and item IDs come from the Variable Library and `fabric/parameter.yml`. Never hard-code workspace or item IDs.
- **Tables are not in Git.** DE2's DDL notebooks are idempotent and run after every deploy.

## 5. Team

| Agent | File | Owns |
|---|---|---|
| DE Manager | `.claude/agents/de-manager.md` | Triage, sub-issues, reviews, contracts approval, CI/CD, integration tests, release PRs |
| DE1 Pipeline Engineer | `.claude/agents/de1-pipeline.md` | Ingestion, Eventstreams, pipelines, streaming notebooks, SCD loads, error handling, idempotency |
| DE2 Data Modeler | `.claude/agents/de2-modeler.md` | Data contracts, DDL, constraints, keys, Delta properties, table versioning |
| BI Developer | `.claude/agents/bi-developer.md` | Semantic model (TMDL), reports (PBIR), RLS, Fabric App |
| Data Scientist | `.claude/agents/data-scientist.md` | Forecasts, predictions, prescriptive tables, model cards |
| Bhuvanesh | — | Product owner. Merges to `main`, approves Prod, handles `owner:me` cards by hand |

**Contract-first:**
1. DE2 writes `contracts/<layer>/<table>.yml`.
2. The Manager approves it.
3. DE1 loads against it; BI and DS build against it.

Changing a contract needs a Manager-approved PR.

## 6. Workflow

**Board columns:** Backlog → Refined → Ready → In progress → In review → In test → Awaiting prod → Done

1. **Requirement.** A stakeholder requirement issue is created from its template.
2. **Refinement.** The Manager splits it into sub-issues labelled `agent:*`, adds acceptance criteria and dependencies, and moves them to Refined.
3. **Ready.** Bhuvanesh moves cards to Ready.
4. **Trigger.** n8n runs Claude Code on the laptop with the matching agent, **one agent at a time**.
5. **Build.** The agent creates a branch, builds and tests in its feature workspace, and opens a PR to `dev` with `Closes #n`. The card moves to In review.
6. **Review.** The Manager reviews and merges to `dev`. CI deploys to Dev.
7. **Release PR.** The Manager opens a `dev → main` release PR. Bhuvanesh merges it, CI deploys to Test and runs the tests, and the card moves to Awaiting prod.
8. **Prod.** Bhuvanesh approves the `prod` environment run. The card moves to Done.

**Manual work:** cards labelled `owner:me` are skipped by n8n. Bhuvanesh follows the same branch → PR → CI flow for them.

## 7. Guardrails (all agents)

- **Never touch Prod.** No Prod credentials exist on the agent side.
- **Never merge to `main`.** Never push to `dev` or `main` directly.
- **Stay inside the paths** your agent file owns. Need something outside them? Ask in the issue.
- **No secrets in files.** Use `${VAR}` from `.env`. New secrets go into `infra/.env.example` as a placeholder, and you tell Bhuvanesh.
- **Never delete data or workspaces.** Never change tenant or capacity settings.
- **When unclear, stop and ask** in the issue. Don't guess.
- **Keep capacity usage low.** Stop Spark sessions and streaming jobs when done. The trial capacity is shared.

## 8. Open decisions

- Timeline: 1 or 2 weeks (2 recommended).
- Whether the Manager may move low-risk cards to Ready itself.
