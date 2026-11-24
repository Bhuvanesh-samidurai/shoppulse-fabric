import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "infra" / "ci"))
from deploy import stage


def test_stage_drops_build_artefacts_and_keeps_item_files(tmp_path):
    src = tmp_path / "data"
    nb = src / "nb_x.Notebook"
    (nb / "__pycache__").mkdir(parents=True)
    (nb / "__pycache__" / "notebook-content.cpython-311.pyc").write_bytes(b"\x00\x01")
    (nb / ".platform").write_text("{}")
    (nb / "notebook-content.py").write_text("print(1)")
    (nb / "stray.pyc").write_bytes(b"\x00")
    (src / "lh.Lakehouse" / ".ipynb_checkpoints").mkdir(parents=True)
    (src / "lh.Lakehouse" / ".platform").write_text("{}")

    out = stage(src, tmp_path / "staged")

    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == ["lh.Lakehouse/.platform", "nb_x.Notebook/.platform", "nb_x.Notebook/notebook-content.py"]
    assert (nb / "__pycache__").exists(), "the source folder is left untouched"
