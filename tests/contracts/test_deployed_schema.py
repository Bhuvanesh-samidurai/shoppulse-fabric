"""Deployed tables equal their contracts (columns, types, nullability, partitions, Delta properties,
CHECK constraints) and the Bronze shortcuts exist. Reads the Delta logs straight from OneLake, no Spark.

Skipped unless a workspace is given:

    $env:SHOPPULSE_SCHEMA_WORKSPACE = "shoppulse-feat-de2"
    $env:SHOPPULSE_SECRETS_FILE = "infra/secrets/sp-agent-de2.env"   # or AZURE_* env vars (CI)
    python -m pytest tests/contracts/test_deployed_schema.py -q
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = os.environ.get("SHOPPULSE_SCHEMA_WORKSPACE")
pytestmark = pytest.mark.skipif(not WORKSPACE, reason="set SHOPPULSE_SCHEMA_WORKSPACE to check a deployed workspace")
DELTA_TYPE = {"int": "integer", "bigint": "long", "tinyint": "byte"}
API = "https://api.fabric.microsoft.com/v1"


def _contracts():
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "contracts").rglob("*.yml"))]


@pytest.fixture(scope="module")
def onelake():
    deltalake = pytest.importorskip("deltalake")
    requests = pytest.importorskip("requests")
    from azure.identity import ClientSecretCredential

    sys.path.insert(0, str(ROOT / "infra" / "ci"))
    from deploy import load_secrets

    s = load_secrets(os.environ.get("SHOPPULSE_SECRETS_FILE"))
    cred = ClientSecretCredential(s["AZURE_TENANT_ID"], s["AZURE_CLIENT_ID"], s["AZURE_CLIENT_SECRET"])
    api_h = {"Authorization": "Bearer " + cred.get_token("https://api.fabric.microsoft.com/.default").token}
    storage_token = cred.get_token("https://storage.azure.com/.default").token
    ws = next(w for w in requests.get(f"{API}/workspaces", headers=api_h, timeout=60).json()["value"] if w["displayName"] == WORKSPACE)
    items = requests.get(f"{API}/workspaces/{ws['id']}/items?type=Lakehouse", headers=api_h, timeout=60).json()["value"]
    lakehouses = {i["displayName"]: i["id"] for i in items}

    def open_table(lakehouse, schema, name):
        uri = f"abfss://{ws['id']}@onelake.dfs.fabric.microsoft.com/{lakehouses[lakehouse]}/Tables/{schema}/{name}"
        return deltalake.DeltaTable(uri, storage_options={"bearer_token": storage_token, "use_fabric_endpoint": "true"})

    def shortcuts(lakehouse):
        url = f"{API}/workspaces/{ws['id']}/items/{lakehouses[lakehouse]}/shortcuts"
        return {x["name"] for x in requests.get(url, headers=api_h, timeout=60).json().get("value", [])}

    return open_table, shortcuts


CONTRACTS = _contracts()
TABLES = [d for d in CONTRACTS if d["load"] != "shortcut"]


@pytest.mark.parametrize("d", TABLES, ids=[d["table"] for d in TABLES])
def test_table_matches_contract(onelake, d):
    open_table, _ = onelake
    layer, name = d["table"].split(".")
    t = open_table(d["lakehouse"], "ops" if layer == "ops" else "dbo", name)
    fields = {f.name: f for f in t.schema().fields}
    for c in d["columns"]:
        assert c["name"] in fields, f"{d['table']}: column {c['name']} missing"
        f = fields[c["name"]]
        assert f.type.type == DELTA_TYPE.get(c["type"], c["type"]), f"{d['table']}.{c['name']}: {f.type.type} != {c['type']}"
        assert f.nullable == c["nullable"], f"{d['table']}.{c['name']}: nullable {f.nullable} != {c['nullable']}"
    meta = t.metadata()
    assert list(meta.partition_columns) == d["partition_by"]
    conf = meta.configuration
    for k, v in d["delta_properties"].items():
        assert conf.get(k) == (str(v).lower() if isinstance(v, bool) else str(v)), f"{d['table']}: {k}"
    assert conf.get("shoppulse.contract_version") == d["version"]
    for x in d.get("constraints", []):
        assert f"delta.constraints.{x['name'].lower()}" in conf, f"{d['table']}: constraint {x['name']} missing"


def test_bronze_shortcuts_exist(onelake):
    _, shortcuts = onelake
    want = {d["table"].split(".")[1] for d in CONTRACTS if d["load"] == "shortcut"}
    missing = want - shortcuts("lh_bronze")
    assert not missing, f"missing Bronze shortcuts: {sorted(missing)}"
