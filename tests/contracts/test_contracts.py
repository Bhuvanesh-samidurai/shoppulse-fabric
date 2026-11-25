"""Static checks on data contracts (owner: DE2). Run: python -m pytest tests/contracts -q

These tests need no Fabric access. The "deployed schema equals contract" check runs later
against the feature workspace once the DDL notebooks exist (see .claude/agents/de2-modeler.md).
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
LAYERS = {"bronze", "silver", "gold", "ops"}
REQUIRED = ["table", "version", "owner", "layer", "lakehouse", "description", "grain", "load", "scd",
            "primary_key", "business_key", "columns", "partition_by", "delta_properties"]
TYPE_RE = re.compile(r"^(string|int|bigint|tinyint|double|boolean|date|timestamp|decimal\(\d{1,2},\d{1,2}\))$")
NAME_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")
SCD2_COLS = {"valid_from", "valid_to", "is_current", "row_hash"}
RETENTION = {"delta.logRetentionDuration": "interval 7 days", "delta.deletedFileRetentionDuration": "interval 2 days"}  # SPEC §4 F2 rules


def _load() -> dict[str, dict]:
    out = {}
    for p in sorted(CONTRACTS.rglob("*.yml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        d["_path"] = p
        out[d["table"]] = d
    return out


ALL = _load()
IDS = sorted(ALL)


def cols(d):
    return {c["name"]: c for c in d["columns"]}


def test_contracts_exist():
    assert len(ALL) >= 40


@pytest.mark.parametrize("t", IDS)
def test_required_fields_and_naming(t):
    d = ALL[t]
    missing = [k for k in REQUIRED if k not in d]
    assert not missing, f"{t}: missing {missing}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", d["version"]), f"{t}: version must be semver"
    assert d["owner"] == "de2" or t.split(".")[1].startswith(("pred_", "rec_")), f"{t}: owner"
    layer, name = t.split(".")
    assert layer in LAYERS and d["layer"] == layer
    assert d["_path"].parent.name == layer and d["_path"].stem == name, f"{t}: file must be contracts/{layer}/{name}.yml"
    assert d["scd"] in {"none", "1", "2"}
    assert d["load"] in {"shortcut", "append", "merge", "overwrite"}


@pytest.mark.parametrize("t", IDS)
def test_columns_valid(t):
    names = [c["name"] for c in ALL[t]["columns"]]
    assert len(names) == len(set(names)), f"{t}: duplicate columns"
    for c in ALL[t]["columns"]:
        assert NAME_RE.match(c["name"]), f"{t}.{c['name']}: snake_case"
        assert TYPE_RE.match(c["type"]), f"{t}.{c['name']}: unknown type {c['type']}"
        assert isinstance(c["nullable"], bool)


@pytest.mark.parametrize("t", IDS)
def test_keys_exist_and_not_null(t):
    d, cs = ALL[t], cols(ALL[t])
    for k in d["primary_key"] + d["business_key"] + d["partition_by"]:
        assert k in cs, f"{t}: key/partition column {k} not in columns"
    for k in d["primary_key"] + d["business_key"]:
        assert cs[k]["nullable"] is False, f"{t}: key column {k} must be NOT NULL"


@pytest.mark.parametrize("t", IDS)
def test_foreign_keys_resolve(t):
    d, cs = ALL[t], cols(ALL[t])
    for fk in d.get("foreign_keys", []):
        assert fk["column"] in cs, f"{t}: FK column {fk['column']} missing"
        ref_table, ref_col = fk["ref"].rsplit(".", 1)
        assert ref_table in ALL, f"{t}: FK ref table {ref_table} has no contract"
        assert ref_col in cols(ALL[ref_table]), f"{t}: FK ref column {fk['ref']} missing"
        if t.startswith("gold.fact_"):
            assert ALL[ref_table]["primary_key"] == [ref_col], f"{t}: fact FK must point at the dimension's surrogate key"


@pytest.mark.parametrize("t", IDS)
def test_scd2_columns(t):
    d = ALL[t]
    if d["scd"] == "2":
        assert SCD2_COLS <= set(cols(d)), f"{t}: SCD2 needs {sorted(SCD2_COLS)}"


@pytest.mark.parametrize("t", [t for t in IDS if t.startswith("gold.dim_")])
def test_dimensions_have_unknown_member(t):
    assert ALL[t].get("unknown_member") is True, f"{t}: dimensions need an unknown member (-1)"


@pytest.mark.parametrize("t", IDS)
def test_delta_properties(t):
    d = ALL[t]
    if d["load"] == "shortcut":
        assert d.get("retention_note"), f"{t}: shortcut tables document where history lives"
        return
    props = d["delta_properties"]
    for k, v in RETENTION.items():
        assert props.get(k) == v, f"{t}: {k} must be '{v}' (SPEC §4)"
    if d["layer"] in {"silver", "gold"}:
        assert props.get("delta.enableChangeDataFeed") is True, f"{t}: Silver/Gold need change data feed for incremental loads"


@pytest.mark.parametrize("t", IDS)
def test_lineage_metadata(t):
    d, cs = ALL[t], set(cols(ALL[t]))
    if d["layer"] == "bronze" and d["load"] != "shortcut":
        assert {"_ingested_at", "_source", "_batch_id"} <= cs, f"{t}: Bronze needs _ingested_at, _source, _batch_id"
    if d["layer"] in {"silver", "gold"}:
        assert "_updated_at" in cs and "_run_id" in cs, f"{t}: needs _updated_at and _run_id"


def test_no_pii_in_gold():
    leaks = [f"{t}.{c['name']}" for t in IDS if t.startswith("gold.") for c in ALL[t]["columns"] if c.get("pii")]
    assert not leaks, f"PII must not reach Gold (SPEC §4a GDPR): {leaks}"


def test_every_spec_gold_table_has_contract():
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    section = spec.split("### Gold model", 1)[1].split("**Data science outputs", 1)[0]
    wanted = set(re.findall(r"`((?:fact|dim)_[a-z_]+)`", section))
    assert wanted, "could not parse SPEC Gold model"
    missing = sorted(w for w in wanted if f"gold.{w}" not in ALL)
    assert not missing, f"SPEC Gold tables without a contract: {missing}"


def _sql_tables() -> dict[str, list[str]]:
    """Parse CREATE TABLE blocks in sql/*.sql -> {schema.table: [column names]}."""
    out = {}
    for p in sorted((ROOT / "sql").glob("*.sql")):
        for m in re.finditer(r"CREATE TABLE (\w+)\.(\w+) \((.*?)\n\)", p.read_text(encoding="utf-8"), re.DOTALL):
            names = []
            for line in m.group(3).splitlines():
                tok = line.strip().split(" ", 1)[0]
                if tok and tok.upper() not in {"CONSTRAINT", "PERIOD", "--"} and not tok.startswith("--"):
                    names.append(tok.rstrip(","))
            out[f"{m.group(1)}.{m.group(2)}"] = names
    return out


def test_bronze_shortcuts_match_sql_ddl():
    sql = _sql_tables()
    assert sql, "no CREATE TABLE found in sql/"
    for t in IDS:
        d = ALL[t]
        if d["load"] != "shortcut":
            continue
        src = d["source"].rsplit(": ", 1)[1]
        assert src in sql, f"{t}: source {src} not created in sql/"
        assert [c["name"] for c in d["columns"]] == sql[src], f"{t}: columns differ from {src} in sql/"
