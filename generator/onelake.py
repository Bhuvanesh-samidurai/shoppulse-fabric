"""Upload generator output to a lakehouse's Files area in OneLake (owner: DE1).

    python -m generator.onelake --agent de1 --what ref                # -> shoppulse-feat-de1/lh_bronze/Files/landing/ref/
    python -m generator.onelake --agent de1 --what ref --dir generator/data/out/sample_1

Runs as the agent's own service principal (infra/secrets/sp-agent-<agent>.env), so it can only
write to that agent's feature workspace (SPEC §4a). Dev/Test/Prod uploads are decided in #26.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from generator import OUT, ROOT

ACCOUNT = "https://onelake.dfs.fabric.microsoft.com"
REF_FILES = ["ref_geography_de", "ref_category_translation", "ref_holiday_de"]


def credential(agent: str):
    from azure.identity import ClientSecretCredential

    path = ROOT / "infra" / "secrets" / f"sp-agent-{agent}.env"
    if not path.exists():
        sys.exit(f"{path} not found (created by infra/bootstrap/bootstrap.ps1)")
    env = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return ClientSecretCredential(env["AZURE_TENANT_ID"], env["AZURE_CLIENT_ID"], env["AZURE_CLIENT_SECRET"])


def upload(workspace: str, lakehouse: str, files: list[pathlib.Path], target_dir: str, cred) -> list[str]:
    from azure.storage.filedatalake import DataLakeServiceClient

    fs = DataLakeServiceClient(ACCOUNT, credential=cred).get_file_system_client(workspace)
    done = []
    for f in files:
        path = f"{lakehouse}.Lakehouse/Files/{target_dir}/{f.name}"
        with f.open("rb") as data:
            fs.get_file_client(path).upload_data(data, overwrite=True)
        done.append(path)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", required=True, choices=["mgr", "de1", "de2", "bi", "ds"])
    ap.add_argument("--what", required=True, choices=["ref"])
    ap.add_argument("--dir", type=pathlib.Path, default=OUT / "sample_0.05")
    a = ap.parse_args()
    files = [a.dir / f"{n}.parquet" for n in REF_FILES]
    missing = [f for f in files if not f.exists()]
    if missing:
        sys.exit(f"missing {missing}: run python -m generator.relocate first")
    for p in upload(f"shoppulse-feat-{a.agent}", "lh_bronze", files, "landing/ref", credential(a.agent)):
        print("uploaded", p)


if __name__ == "__main__":
    main()
