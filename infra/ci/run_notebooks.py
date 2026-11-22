"""Run Fabric notebooks matching a name pattern, one at a time, and fail if any fails.

CD uses it after each deploy to run DE2's idempotent DDL notebooks (nb_ddl_*) and the
smoke test (nb_ops_smoke). One at a time on purpose: F2 allows one Spark session (SPEC §4).

    python infra/ci/run_notebooks.py --workspace shoppulse-data-dev --pattern "nb_ddl_*"
"""
from __future__ import annotations

import argparse
import fnmatch
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from deploy import load_secrets

API = "https://api.fabric.microsoft.com/v1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--pattern", required=True, help='fnmatch pattern, e.g. "nb_ddl_*"')
    ap.add_argument("--secrets-file")
    ap.add_argument("--timeout-min", type=int, default=20)
    args = ap.parse_args()

    from azure.identity import ClientSecretCredential

    s = load_secrets(args.secrets_file)
    cred = ClientSecretCredential(s["AZURE_TENANT_ID"], s["AZURE_CLIENT_ID"], s["AZURE_CLIENT_SECRET"])
    h = {"Authorization": "Bearer " + cred.get_token("https://api.fabric.microsoft.com/.default").token}

    ws = next((w for w in requests.get(f"{API}/workspaces", headers=h, timeout=60).json()["value"]
               if w["displayName"] == args.workspace), None)
    if not ws:
        sys.exit(f"Workspace {args.workspace} not visible to this identity")
    items = requests.get(f"{API}/workspaces/{ws['id']}/items?type=Notebook", headers=h, timeout=60).json()["value"]
    notebooks = sorted((i for i in items if fnmatch.fnmatchcase(i["displayName"], args.pattern)),
                       key=lambda i: i["displayName"])
    if not notebooks:
        print(f"No notebooks matching {args.pattern} in {args.workspace}; nothing to run")
        return 0

    failed = []
    for nb in notebooks:
        print(f"Running {nb['displayName']} ...", flush=True)
        r = requests.post(f"{API}/workspaces/{ws['id']}/items/{nb['id']}/jobs/instances?jobType=RunNotebook",
                          headers=h, timeout=60)
        if r.status_code != 202:
            print(f"  could not start: {r.status_code} {r.text[:300]}")
            failed.append(nb["displayName"])
            continue
        loc, deadline, status, job = r.headers["Location"], time.time() + args.timeout_min * 60, "TimedOut", {}
        while time.time() < deadline:
            time.sleep(15)
            job = requests.get(loc, headers=h, timeout=60).json()
            status = job.get("status")
            if status in ("Completed", "Failed", "Cancelled", "Deduped"):
                break
        print(f"  {status}")
        if status != "Completed":
            print(f"  failure: {job.get('failureReason')}")
            failed.append(nb["displayName"])

    if failed:
        sys.exit(f"Failed notebooks: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
