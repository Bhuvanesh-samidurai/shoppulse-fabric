"""Deploy fabric/data and fabric/bi to a ShopPulse environment with fabric-cicd.

Used by CI (dev / test / prod, credentials from AZURE_* env vars) and by agents
for their own feature workspace (credentials from their secrets file):

    python infra/ci/deploy.py --env dev
    python infra/ci/deploy.py --env feat --agent de1 --secrets-file infra/secrets/sp-agent-de1.env

Rules (SPEC §4):
- feat: both parts go to shoppulse-feat-<agent>; nothing is unpublished.
- dev/test/prod: data -> shoppulse-data-<env>, bi -> shoppulse-bi-<env>; items no longer
  in the repo are unpublished, except data items (lakehouse, SQL DB, eventhouse,
  KQL DB, warehouse), which fabric-cicd never deletes unless a feature flag is set.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARTS = ("data", "bi")
AGENTS = ("mgr", "de1", "de2", "bi", "ds")


def workspace_for(env: str, part: str, agent: str | None) -> str:
    if env == "feat":
        return f"shoppulse-feat-{agent}"
    return f"shoppulse-{part}-{env}"


def load_secrets(path: str | None) -> dict[str, str]:
    values = dict(os.environ)
    if path:
        for line in pathlib.Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    missing = [k for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET") if not values.get(k)]
    if missing:
        sys.exit(f"Missing credentials: {', '.join(missing)} (set env vars or pass --secrets-file)")
    return values


def has_items(folder: pathlib.Path) -> bool:
    return folder.is_dir() and any(folder.rglob(".platform"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", required=True, choices=["feat", "dev", "test", "prod"])
    ap.add_argument("--agent", choices=AGENTS, help="required for --env feat")
    ap.add_argument("--part", choices=[*PARTS, "all"], default="all")
    ap.add_argument("--secrets-file", help="KEY=VALUE file with AZURE_* credentials")
    args = ap.parse_args()
    if args.env == "feat" and not args.agent:
        ap.error("--agent is required with --env feat")

    from azure.identity import ClientSecretCredential
    from fabric_cicd import (
        FabricWorkspace,
        change_log_level,
        publish_all_items,
        unpublish_all_orphan_items,
    )

    if os.environ.get("RUNNER_DEBUG") == "1":
        change_log_level("DEBUG")

    s = load_secrets(args.secrets_file)
    cred = ClientSecretCredential(s["AZURE_TENANT_ID"], s["AZURE_CLIENT_ID"], s["AZURE_CLIENT_SECRET"])

    parts = PARTS if args.part == "all" else (args.part,)
    for part in parts:
        folder = ROOT / "fabric" / part
        ws_name = workspace_for(args.env, part, args.agent)
        if not has_items(folder):
            print(f"[{part}] no items in fabric/{part}, skipped")
            continue
        print(f"[{part}] deploying fabric/{part} -> {ws_name} (environment '{args.env}')")
        try:
            ws = FabricWorkspace(
                workspace_name=ws_name,
                repository_directory=str(folder),
                environment=args.env,
                token_credential=cred,
            )
            publish_all_items(ws)
            if args.env != "feat":
                unpublish_all_orphan_items(ws)
        except Exception as exc:
            print(
                f"\nDeploy to {ws_name} failed: {exc}\n"
                "Common causes: capacity fabcapacity1 is paused (resume it in the Azure portal), "
                "or this identity lacks a role on the workspace (see SPEC §4a).",
                file=sys.stderr,
            )
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
