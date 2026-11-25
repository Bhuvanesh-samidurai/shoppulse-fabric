# CI/CD (owner: DE Manager)

## Flow

| Trigger | Workflow / job | Target | Identity |
|---|---|---|---|
| PR to `dev` or `main` | `ci` / `checks` | Lint, contract YAML, unit tests | — |
| PR to `dev` or `main` | `ci` / `path-guard` | Branch prefix may only change its own paths (`infra/ci/ownership.yml`) | — |
| Push to `dev` (merge) | `deploy` / `dev` | `shoppulse-data-dev`, `shoppulse-bi-dev` | `sp-ci-dev` |
| Push to `main` (release) | `deploy` / `test` | `shoppulse-data-test`, `shoppulse-bi-test`, plus integration tests | `sp-ci-test` |
| After `test` succeeds | `deploy` / `prod` | `shoppulse-data-prod`, `shoppulse-bi-prod` | `sp-ci-prod`; **waits for Bhuvanesh's approval** (GitHub Environment `prod`) |
| Agent, by hand | `infra/ci/deploy.py --env feat --agent <x>` | `shoppulse-feat-<x>` | The agent's own service principal |

**Every deploy runs these steps:**
1. `deploy.py`: fabric-cicd publishes `fabric/data` and `fabric/bi`.
2. `run_notebooks.py "nb_ddl_*"`: DE2's idempotent DDL notebooks, one at a time.
3. `run_notebooks.py nb_ops_smoke`: the smoke test.

Deploys only trigger when `fabric/**`, `infra/ci/**` or `deploy.yml` changed. Otherwise use **Run workflow** (`workflow_dispatch`).

**The capacity must be running.** If `fabcapacity1` is paused, the deploy fails with a hint. Resume it and re-run the job.

## For agents: deploy your branch to your feature workspace

```powershell
.\.venv\Scripts\python.exe infra\ci\deploy.py --env feat --agent de1 --secrets-file infra\secrets\sp-agent-de1.env
.\.venv\Scripts\python.exe infra\ci\run_notebooks.py --workspace shoppulse-feat-de1 --pattern "nb_ingest_*" --secrets-file infra\secrets\sp-agent-de1.env
```

**Feature deploys never delete anything.** Dev, Test and Prod deploys unpublish items that were removed from the repo. Data items (Lakehouse, SQL DB, Eventhouse, KQL DB, Warehouse) are never deleted automatically, because fabric-cicd leaves them alone unless a feature flag is set.

## Repo layout for Fabric items

```
fabric/data/   -> shoppulse-data-<env>  (lakehouses, notebooks, pipelines, eventstreams, eventhouse, SQL DB, UDFs, variable library)
fabric/bi/     -> shoppulse-bi-<env>    (semantic model, reports, KQL dashboard)
```

Items use Fabric's Git format: `<name>.<Type>/` with a `.platform` file. Naming conventions decide ownership (see `infra/ci/ownership.yml`).
- Notebooks: `nb_ingest_*`, `nb_silver_*`, `nb_gold_load_*` (DE1); `nb_ddl_*` (DE2); `nb_ml_*` and `nb_eda_*` (DS); `nb_ops_*` (Manager).
- Lakehouses: `lh_bronze`, `lh_silver`, `lh_gold`, `lh_ml` (DE2).

## Per-environment settings

- **Variable Library `vl_shoppulse`** (`fabric/data/vl_shoppulse.VariableLibrary/`).
  - Value sets: `dev`, `test`, `prod`. fabric-cicd activates the one matching the environment; feature workspaces use the default values.
  - Variables: `env_name`, `sample_fraction` (0.05 in feature and Dev, 1.0 in Test and Prod), `microbatch_minutes` (15).
  - In notebooks: `notebookutils.variableLibrary.get("$(/**/vl_shoppulse/<name>)")`.
- **`parameter.yml`** (`fabric/data/` or `fabric/bi/`): fabric-cicd find/replace for IDs that differ per workspace.
  - Example: a notebook's default lakehouse becomes `$items.Lakehouse.lh_silver.$id`.
  - For BI: the semantic model's Direct Lake source points at the Gold lakehouse in the data workspace of the same environment.
  - Create the file only when the first entry is needed. fabric-cicd rejects an empty or comment-only file.

## Item support spike (2026-11-22)

**Method:** `sp-agent-mgr` created and deleted each item type in `shoppulse-feat-mgr` through the Fabric REST API. fabric-cicd 1.4.0 lists all of them as deployable.

| Item type | Service principal create/delete | fabric-cicd |
|---|---|---|
| Lakehouse | ✅ | ✅ (never auto-deleted) |
| Notebook | ✅ | ✅ (deployed + run end to end) |
| DataPipeline | ✅ | ✅ |
| Environment | ✅ | ✅ |
| VariableLibrary | ✅ | ✅ (deployed, value set activated) |
| Eventhouse | ✅ | ✅ (never auto-deleted) |
| KQLDatabase | ✅ | ✅ (never auto-deleted) |
| Eventstream | ✅ | ✅ |
| SQLDatabase | ✅ | ✅ (never auto-deleted) |
| UserDataFunction | ✅ | ✅ |
| Reflex (Activator) | ✅ | ✅ |
| MLExperiment | ✅ | ✅ |
| KQLDashboard | ✅ | ✅ |
| SemanticModel, Report | Not spiked. They need a definition and a source. | ✅. Verify when BI commits the first model. |

**Result:** nothing needs a `fab` fallback script so far. Still to re-check when they first appear:
- Eventstream sources and destinations, which carry connection IDs and may need `parameter.yml`.
- Semantic model rebinding.

## Local tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r infra\ci\requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```
