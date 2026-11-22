# ShopPulse: real-time e-commerce and supply chain on Microsoft Fabric

A real-time fulfilment platform for a fictional German online retailer. It covers the shop, three warehouses (Hamburg, Dortmund, Munich) and the parcel carriers. It shows what is happening now, predicts late deliveries and demand, and recommends actions. Managers write decisions back through a Fabric App.

> **Status:** in build. See the [project board](../../projects) and [SPEC.md](SPEC.md).

## Architecture

```
Shop OLTP (SQL DB) ─mirroring─┐
WMS events ─────Eventstream───┼─► Bronze ─► Silver (+ LLM enrichment) ─► Gold (star schema)
Carrier events ─Eventstream───┘                                              │
                       └─► Eventhouse (KQL, hot path)                         │
   Direct Lake semantic model ◄───────────────────────────────────────────────┘
   → Power BI report · Real-Time Dashboard · Activator alerts · Fabric App with write-back
```

**Stack:**
- **Fabric:** Lakehouse (Delta), Spark Structured Streaming, Eventstream, Eventhouse/KQL, Fabric SQL Database, Direct Lake, Power BI, User Data Functions, Activator.
- **ML:** MLflow, LightGBM.
- **CI/CD:** GitHub Actions + `fabric-cicd`.

## Data

The base is the public Olist e-commerce dataset. It is **relocated to German geography**: postcodes and Bundesländer, EUR with 19% / 7% VAT, and German holidays. **Warehouse and carrier events are simulated** from each order's real timestamps. No real customer data is used.

## How it's built

A five-agent AI data team: DE Manager, Pipeline Engineer, Data Modeler, BI Developer and Data Scientist (see [`.claude/agents/`](.claude/agents/)). They work in feature, Dev and Test workspaces. A human product owner reviews every release and is the only one who can deploy to Prod. See [SPEC.md](SPEC.md) §4–§6.

## Repo layout

| Path | What |
|---|---|
| `contracts/` | Data contracts per table (schema, keys, grain, SCD, Delta properties) |
| `fabric/data/`, `fabric/bi/` | Fabric items in Git format, deployed to the data and BI workspaces |
| `generator/` | Olist download, German relocation, live replayer |
| `sql/` | OLTP and write-back DDL |
| `kql/` | Eventhouse tables, policies, functions |
| `tests/` | Pipeline, contract, integration and DAX tests |
| `docs/` | Architecture, data model, model cards, releases |
