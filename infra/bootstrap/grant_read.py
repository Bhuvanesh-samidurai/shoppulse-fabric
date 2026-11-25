"""Grant agents OneLake *read* on Dev/Test lakehouses exactly per SPEC §4a (issue #18). Idempotent.

Runs as Bhuvanesh (browser sign-in, cached). Workspace Viewer gives no OneLake file access; this adds
one OneLake data access role `AgentRead` per lakehouse whose members are the agent service principals
the matrix allows. The lakehouse's other roles (e.g. DefaultReader) are kept unchanged. Never Prod.

    .venv\\Scripts\\python.exe infra\\bootstrap\\grant_read.py --dry-run
    .venv\\Scripts\\python.exe infra\\bootstrap\\grant_read.py
"""
from __future__ import annotations

import argparse
import sys

import requests
from azure.identity import InteractiveBrowserCredential, TokenCachePersistenceOptions

TENANT = "c830d8b6-3b34-4cf4-8b77-ada50aea1e53"
LOGIN_HINT = "bhuvi_fabric@buvanesh80outlook.onmicrosoft.com"
API = "https://api.fabric.microsoft.com/v1"
ROLE = "AgentRead"

# SPEC §4a access matrix, read column. Keep in sync with SPEC; changing it is a permissions change.
MATRIX = {
    "shoppulse-data-dev": {
        "lh_bronze": ["sp-agent-mgr", "sp-agent-de1", "sp-agent-de2"],
        "lh_silver": ["sp-agent-mgr", "sp-agent-de1", "sp-agent-de2", "sp-agent-ds"],
        "lh_gold": ["sp-agent-mgr", "sp-agent-de1", "sp-agent-de2", "sp-agent-ds", "sp-agent-bi"],
    },
    "shoppulse-data-test": {lh: ["sp-agent-mgr", "sp-agent-de1", "sp-agent-de2", "sp-agent-ds", "sp-agent-bi"]
                            for lh in ("lh_bronze", "lh_silver", "lh_gold")},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cred = InteractiveBrowserCredential(tenant_id=TENANT, login_hint=LOGIN_HINT,
                                        cache_persistence_options=TokenCachePersistenceOptions(name="shoppulse-bootstrap"))
    fab = {"Authorization": "Bearer " + cred.get_token("https://api.fabric.microsoft.com/.default").token}
    graph = {"Authorization": "Bearer " + cred.get_token("https://graph.microsoft.com/.default").token}

    names = sorted({sp for lhs in MATRIX.values() for sps in lhs.values() for sp in sps})
    oid = {}
    for n in names:
        r = requests.get("https://graph.microsoft.com/v1.0/servicePrincipals", headers=graph, timeout=60,
                         params={"$filter": f"displayName eq '{n}'", "$select": "id,displayName"}).json()["value"]
        if len(r) != 1:
            sys.exit(f"{n}: expected 1 service principal, found {len(r)}")
        oid[n] = r[0]["id"]

    workspaces = {w["displayName"]: w["id"] for w in requests.get(f"{API}/workspaces", headers=fab, timeout=60).json()["value"]}
    for ws_name, lakehouses in MATRIX.items():
        if ws_name.endswith("-prod"):
            sys.exit("refusing to touch Prod")
        ws = workspaces[ws_name]
        items = {i["displayName"]: i["id"] for i in requests.get(f"{API}/workspaces/{ws}/items?type=Lakehouse", headers=fab, timeout=60).json()["value"]}
        for lh, members in lakehouses.items():
            if lh not in items:
                print(f"  skipped  {ws_name}/{lh}: not deployed yet (re-run after the next release)")
                continue
            url = f"{API}/workspaces/{ws}/items/{items[lh]}/dataAccessRoles"
            current = requests.get(url, headers=fab, timeout=60).json().get("value", [])
            others = [{k: v for k, v in r.items() if k in ("name", "decisionRules", "members")} for r in current if r["name"] != ROLE]
            existing = next((r for r in current if r["name"] == ROLE), None)
            have = sorted(m["objectId"] for m in (existing or {}).get("members", {}).get("microsoftEntraMembers", []))
            want = sorted(oid[m] for m in members)
            label = f"{ws_name}/{lh}: {', '.join(members)}"
            if have == want:
                print(f"  ok       {label}")
                continue
            print(f"  {'would set' if args.dry_run else 'set'}  {label}")
            if args.dry_run:
                continue
            role = {"name": ROLE,
                    "decisionRules": [{"effect": "Permit", "permission": [
                        {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
                        {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
                    "members": {"microsoftEntraMembers": [{"tenantId": TENANT, "objectId": oid[m]} for m in members]}}
            r = requests.put(url, headers=fab, json={"value": others + [role]}, timeout=60)
            if not r.ok:
                sys.exit(f"{label}: {r.status_code} {r.text[:400]}")
    print("done" if not args.dry_run else "dry run done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
