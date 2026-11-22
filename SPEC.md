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

Each shared environment is split into a **data** workspace (lakehouses, notebooks, pipelines, eventstreams, eventhouse, SQL DB) and a **BI** workspace (semantic model, reports, dashboard, app).

| Env | Fabric workspaces | Branch | Deployed by | Data |
|---|---|---|---|---|
| Feature | `shoppulse-feat-mgr`, `-de1`, `-de2`, `-bi`, `-ds` | `<agent>/<issue>-<name>` | The agent, into its own workspace | 5% sample |
| Dev | `shoppulse-data-dev`, `shoppulse-bi-dev` | `dev` | `sp-ci-dev` on merge | 5% sample |
| Test | `shoppulse-data-test`, `shoppulse-bi-test` | `main` | `sp-ci-test` on merge | Full replay |
| Prod | `shoppulse-data-prod`, `shoppulse-bi-prod` | `main` (approved run) | `sp-ci-prod`, only after **Bhuvanesh** approves the GitHub Environment `prod` | Full replay |

Data science outputs land in their own lakehouse, `lh_ml`, exposed to Gold through shortcuts.

- **Capacity:** all 11 workspaces sit on one **F2** capacity (Azure, paid from trial credit). Pause it whenever nobody is working. Record the demo before the credit runs out.
- **F2 design rules** (about 4 Spark cores, short bursts up to about 12):
  - **One Spark session at a time** across all workspaces. Agents run one at a time.
  - **Small Spark pool:** small nodes, at most 2, idle timeout 10 minutes. Stop sessions when done.
  - **No always-on Spark streaming.**
    - Real-time path: Eventstream writes directly to Bronze and to Eventhouse.
    - Silver and Gold run as scheduled micro-batches every 15 minutes (only while live data runs).
  - **Live data in one environment at a time.** Dev while building, Test only during release tests, Prod for the demo. Stop the replayer otherwise.
  - **Eventhouse:** minimum consumption off, hot cache 1 day, retention 3 days.
  - **Delta retention:** log 7 days, deleted files 2 days. Run `OPTIMIZE` weekly.
  - **If throttled:** first reduce the sample size (5% → 2%), then the micro-batch frequency, then retention. Check the Capacity Metrics app.
  - **Power BI on F2:** report viewers need a Pro or trial licence (below F64). This is fine for the portfolio.
- **Per-environment IDs:** connection and item IDs come from the Variable Library and `fabric/parameter.yml`. Never hard-code workspace or item IDs.
- **Tables are not in Git.** DE2's DDL notebooks are idempotent and run after every deploy.

## 4a. Identities and access

**Identities.** One Entra service principal per agent, plus three for CI. No agent ever uses Bhuvanesh's login.

| Identity | Used by | Where the secret lives |
|---|---|---|
| Bhuvanesh | Entra Global Admin, Fabric Admin, Azure Owner, GitHub owner | — |
| `sp-agent-mgr`, `sp-agent-de1`, `sp-agent-de2`, `sp-agent-bi`, `sp-agent-ds` | That agent only | Key Vault `kv-shoppulse`, or one file per agent under `infra/secrets/` (git-ignored). Only that agent's secret is mounted into its run container. |
| `sp-ci-dev`, `sp-ci-test` | GitHub Actions deploy jobs | GitHub repository secrets |
| `sp-ci-prod` | The Prod deploy job only | GitHub Environment `prod` secrets |

**Access matrix.** W = write, R = read only, — = no access.

| Area | Bhuvanesh | Manager | DE1 | DE2 | BI | DS |
|---|---|---|---|---|---|---|
| Own feature workspace | Admin | W | W | W | W | W |
| DE1 and DE2 feature workspaces | Admin | W | own only | own only | — | — |
| Dev data: Bronze, Silver | Admin | R | R | R | — | R (Silver only) |
| Dev data: Gold | Admin | R | R | R | R | R |
| Dev data: `lh_ml` | Admin | R | — | R | R | R |
| Dev SQL DB | Admin | R | R (DML in feature) | R (schema owner in feature) | — | — |
| Dev BI workspace | Admin | R | — | — | R | R |
| Test (data + BI) | Admin | R | R | R | R | R |
| Prod (data + BI) | Admin | — | — | — | — | — |
| Entra, Azure, capacity, tenant settings | Owner | — | — | — | — | — |

- **Writes to Dev, Test and Prod come only from CI service principals.** Agents write only in feature workspaces.
- **Mechanisms:**
  - Workspace roles for feature workspaces and CI.
  - Item sharing with ReadAll for read access to particular lakehouses.
  - OneLake security roles for read-only users who need specific tables only.
  - SQL `GRANT`/`DENY` on SQL endpoints and the SQL DB.
- **Known gap: Lakehouse write access includes schema changes,** so Fabric can't separate DE2's schemas from DE1's loads there. The guard is the process: DE1 writes with schema evolution off, contract tests fail on drift, and CI blocks the PR.

**Code governance.** All agents commit under Bhuvanesh's GitHub account, so the branch prefix is the agent identity.
- **Path guard in CI:** a branch with prefix `<agent>/` may only change that agent's paths (§5 and the agent files).
- **CODEOWNERS:** Bhuvanesh owns everything. The Manager reviews `contracts/`.

**Business data governance:**
- **Domain:** Fabric domain "Sales & Fulfilment".
- **Endorsement:** Gold lakehouse and semantic model are Certified by Bhuvanesh.
- **Lineage view** is used for impact checks.
- **GDPR:**
  - Customer data is pseudonymised in Silver.
  - Object-level security (OLS) hides personal columns.
  - RLS filters by Bundesland.
  - There's a deletion-request flow, and retention is defined per contract.
- **Stakeholders** use the Fabric App as viewers. Write-back goes through a User Data Function with EXECUTE on `wb_*` tables only.
- **Out of scope:** sensitivity labels need Purview / Microsoft 365 E5 licences, which this tenant doesn't have.

**Change rule.** Changes to permissions or this section go through a PR that Bhuvanesh approves.

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
