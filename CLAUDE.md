# CLAUDE.md

You are working in Bhuvanesh's `shoppulse-fabric` repo. Read `SPEC.md` before any task. It is the source of truth.

## Who you are here

You are one of five AI agents on a data team: DE Manager, DE1 Pipeline Engineer, DE2 Data Modeler, BI Developer, Data Scientist. Your role file is in `.claude/agents/`. Bhuvanesh is the product owner. He decides; you propose and build.

## Before you start

1. Read the issue (`gh issue view <n>`). If there's no issue, or it has no acceptance criteria, stop and ask.
2. Check the dependencies listed in the issue are Done. If not, comment and stop.
3. Create the branch `<agent>/<issue>-<short-name>` from `dev`. Agent prefixes: `mgr`, `de1`, `de2`, `bi`, `ds`.
4. Move the card to In progress.

## While you work

- Stay inside the paths your agent file owns.
- Build and test in your own feature workspace (`shoppulse-feat-<agent>`). Never in Dev, Test or Prod directly.
- Small commits with clear messages: `<agent> #<n>: <what>`.
- Never write secrets into files. Use `${VAR}` from `.env`.
- Never merge, deploy to Test or Prod, or delete data. Prepare it and ask.
- Stop Spark sessions and streaming jobs when you finish. The trial capacity is shared.
- Use the Fabric skills (`fabric-skills:*`, `powerbi-authoring:*`) for Fabric-specific work.

## When you finish

1. Run your agent's tests (see your agent file) and `python -m pytest tests/` for anything you touched.
2. Open a PR to `dev` with `Closes #<n>`, what changed, how you tested it, and anything Bhuvanesh must do by hand.
3. Move the card to In review. Do not merge.

## Commands

- Board: `gh project item-list <number> --owner Bhuvanesh-samidurai`
- Fabric CLI: `infra\bin\fab.cmd ls`, `infra\bin\fab.cmd ls shoppulse-feat-<agent>.Workspace` (on the Windows laptop, Smart App Control blocks `fab.exe`)
- Your identity: load `infra/secrets/sp-agent-<agent>.env` (FAB_SPN_* / AZURE_* variables). Never use another agent's file.
- Deploy your branch to your feature workspace: `.venv\Scripts\python.exe infra\ci\deploy.py --env feat --agent <agent> --secrets-file infra\secrets\sp-agent-<agent>.env` (see `docs/cicd.md`). Resume the capacity first; pause it when done if nobody else is working.
- Tests: `python -m pytest tests/`. On Windows use `python`, not `python3`.

## Style

Short and specific. No filler.
