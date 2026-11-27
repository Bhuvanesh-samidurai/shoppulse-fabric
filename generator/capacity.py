"""Pause / resume / show the Fabric capacity as Bhuvanesh (owner: DE1 tooling, issue #28).

    python -m generator.capacity status | pause | resume

Uses the cached browser sign-in from infra/bootstrap/grant_read.py (azure-identity), because Azure CLI's
login broker is blocked by Smart App Control on this laptop. Agents' service principals have no rights on
the capacity: this always runs as the human owner.
"""
from __future__ import annotations

import sys
import time

import requests

CAPACITY = "/subscriptions/64c37a4a-37ac-4005-a18f-8922e8868fe4/resourceGroups/fabric_rg/providers/Microsoft.Fabric/capacities/fabcapacity1"
URL = f"https://management.azure.com{CAPACITY}"
API = "api-version=2023-11-01"


def _headers():
    from azure.identity import (
        InteractiveBrowserCredential,
        TokenCachePersistenceOptions,
    )

    cred = InteractiveBrowserCredential(tenant_id="c830d8b6-3b34-4cf4-8b77-ada50aea1e53",
                                        login_hint="bhuvi_fabric@buvanesh80outlook.onmicrosoft.com",
                                        cache_persistence_options=TokenCachePersistenceOptions(name="shoppulse-bootstrap"))
    return {"Authorization": "Bearer " + cred.get_token("https://management.azure.com/.default").token}


def state(h=None) -> str:
    return requests.get(f"{URL}?{API}", headers=h or _headers(), timeout=60).json()["properties"]["state"]


def change(action: str, wait_for: str, timeout_s: int = 300) -> str:
    h = _headers()
    r = requests.post(f"{URL}/{action}?{API}", headers=h, timeout=60)
    if r.status_code not in (200, 202) and state(h) != wait_for:
        sys.exit(f"{action} failed: {r.status_code} {r.text[:200]}")
    t0 = time.time()
    while (s := state(h)) != wait_for and time.time() - t0 < timeout_s:
        time.sleep(10)
    return s


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(state())
    elif cmd == "pause":
        print(change("suspend", "Paused"))
    elif cmd == "resume":
        print(change("resume", "Active"))
    else:
        sys.exit("usage: python -m generator.capacity status|pause|resume")
