import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "infra" / "ci"))
from path_guard import check, load_rules

RULES = load_rules()


def ok(branch, files):
    return check(branch, files, RULES)[0]


def test_agent_inside_own_paths():
    assert ok("de1/3-generator", ["generator/relocate.py", "fabric/data/nb_silver_orders.Notebook/notebook-content.py"])
    assert ok("de2/4-contracts", ["contracts/gold/fact_order_line.yml", "fabric/data/lh_gold.Lakehouse/.platform"])
    assert ok("bi/10-report", ["fabric/bi/ShopPulse.SemanticModel/definition/model.tmdl"])
    assert ok("ds/12-forecast", ["fabric/data/nb_ml_forecast.Notebook/notebook-content.py", "contracts/gold/pred_demand_forecast.yml"])
    assert ok("mgr/2-cicd", [".github/workflows/deploy.yml", "fabric/data/vl_shoppulse.VariableLibrary/variables.json"])


def test_agent_outside_own_paths_fails():
    assert not ok("de1/3-generator", ["contracts/gold/fact_order_line.yml"])      # DE2's contract
    assert not ok("de2/4-contracts", ["fabric/data/nb_silver_orders.Notebook/x.py"])  # DE1's load
    assert not ok("ds/12-forecast", ["contracts/gold/fact_order_line.yml"])         # only pred_/rec_
    assert not ok("bi/10-report", ["fabric/data/lh_gold.Lakehouse/.platform"])
    assert not ok("mgr/2-cicd", ["SPEC.md"])
    assert not ok("de1/3-x", [".claude/agents/de1-pipeline.md"])


def test_unrestricted_and_release_branches():
    assert ok("me/1-anything", ["SPEC.md", ".claude/agents/de1-pipeline.md"])
    assert ok("dev", ["SPEC.md", "fabric/bi/x"])


def test_unknown_prefix_fails():
    assert not ok("feature/foo", ["README.md"])
    assert not ok("main", ["README.md"])
    assert not ok("shared/x", ["generator/.gitkeep"])


def test_protected_files_only_for_me():
    assert not ok("mgr/2-cicd", ["infra/ci/ownership.yml"])
    assert not ok("mgr/2-cicd", [".github/CODEOWNERS"])
    assert not ok("de2/4-x", ["infra/bootstrap/bootstrap.ps1"])
    assert not ok("protected/x", ["README.md"])
    assert ok("me/1-x", ["infra/ci/ownership.yml"])


def test_gitkeep_is_shared():
    assert ok("de1/3-x", ["docs/insights/.gitkeep"])
