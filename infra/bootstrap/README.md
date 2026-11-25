# Platform bootstrap (issue #1, part C)

Creates the identities, workspaces and roles from SPEC §4 / §4a. It's idempotent: re-running it changes only what's missing.

## Before you run it

- [ ] F2 capacity `fabcapacity1` is **running** (resume it in the Azure portal).
- [ ] You can do all of the following:
  - `az login` as `bhuvi_fabric@buvanesh80outlook.onmicrosoft.com`
  - Create app registrations (Global Administrator)
  - Admin the capacity
- [ ] The Fabric tenant setting "Service principals can use Fabric APIs" (plus "create workspaces") is enabled for `sg-shoppulse-sp`.
- [ ] `gh auth status` works (the script sets GitHub secrets).

## Run it (PowerShell, from the repo root)

```powershell
.\infra\bootstrap\bootstrap.ps1 -DryRun      # read-only preview
.\infra\bootstrap\bootstrap.ps1              # apply
.\infra\bootstrap\bootstrap.ps1 -VerifyOnly  # re-check agent access later
```

If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process Bypass` first. That allows scripts in the current window only.

## Output

| Identity | Secret goes to |
|---|---|
| `sp-agent-mgr/de1/de2/bi/ds` | `infra/secrets/sp-agent-<x>.env` (git-ignored). Back these up somewhere safe, e.g. a password manager. |
| `sp-ci-dev`, `sp-ci-test` | GitHub repo secrets `FABRIC_CI_<ENV>_CLIENT_ID/SECRET` |
| `sp-ci-prod` | GitHub environment `prod` secrets only |

Secrets expire after 1 year. To rotate one, delete its file or GitHub secret and re-run the script.

**Not done here:** item-level shares (Gold ReadAll for BI/DS, Silver for DS). They come after the lakehouses exist.

## Fabric CLI on this laptop

Smart App Control blocks the `fab.exe` launcher, so use the wrapper:

```powershell
.\infra\bin\fab.cmd auth login
.\infra\bin\fab.cmd ls
```

## Agent read access to Dev/Test data (issue #18)

Workspace **Viewer** does not grant OneLake file reads. `grant_read.py` adds one OneLake data access role `AgentRead` per lakehouse. Its members are the agent service principals that the SPEC §4a matrix allows. Existing roles such as `DefaultReader` are kept, and Prod is never touched.

```powershell
.\.venv\Scripts\python.exe infra\bootstrap\grant_read.py --dry-run
.\.venv\Scripts\python.exe infra\bootstrap\grant_read.py      # re-run after each release that adds lakehouses
```

- **Sign-in:** it runs as you in a browser and caches the sign-in. Azure CLI's Windows login broker is blocked by Smart App Control on this laptop, so this script doesn't use Azure CLI.
- **Verified in Dev on 2026-11-25:**

| Agent | lh_bronze | lh_silver | lh_gold |
|---|---|---|---|
| Manager, DE1, DE2 | read | read | read |
| DS | denied | read | read |
| BI | denied | denied | read |
