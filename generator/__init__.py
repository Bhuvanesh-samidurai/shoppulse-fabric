"""ShopPulse data generator (owner: DE1): Olist download, German relocation, live replayer."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "generator" / "data"   # git-ignored
RAW = DATA / "raw"
OUT = DATA / "out"


def load_env(path: pathlib.Path = ROOT / "infra" / ".env") -> dict[str, str]:
    """Read KEY=VALUE pairs from infra/.env (never printed, never written anywhere)."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    values[k.strip()] = v.strip()
    return values
