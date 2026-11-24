"""The nb_ddl_* notebooks must be exactly what contracts/_tools/render_ddl.py produces."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_ddl_notebooks_match_contracts():
    r = subprocess.run([sys.executable, str(ROOT / "contracts" / "_tools" / "render_ddl.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, "DDL notebooks are stale. Run: python contracts/_tools/render_ddl.py\n" + r.stdout
