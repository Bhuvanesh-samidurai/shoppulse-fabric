"""Fail a PR whose branch prefix does not own every changed file (SPEC §4a).

    python infra/ci/path_guard.py --branch de1/3-generator --base origin/dev
    python infra/ci/path_guard.py --branch de1/3-x --files generator/a.py SPEC.md   # for tests
"""
from __future__ import annotations

import argparse
import fnmatch
import pathlib
import subprocess
import sys

import yaml

OWNERSHIP = pathlib.Path(__file__).with_name("ownership.yml")
UNRESTRICTED = {"me"}          # Bhuvanesh
RELEASE_BRANCHES = {"dev"}     # release PR dev -> main
META_KEYS = {"shared", "protected"}


def load_rules(path: pathlib.Path = OWNERSHIP) -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text())


def check(branch: str, files: list[str], rules: dict[str, list[str]]) -> tuple[bool, list[str], str]:
    """Return (ok, offending_files, message)."""
    if branch in RELEASE_BRANCHES:
        return True, [], f"release branch '{branch}': all paths allowed"
    prefix = branch.split("/", 1)[0] if "/" in branch else ""
    if prefix in UNRESTRICTED:
        return True, [], f"'{prefix}/' branch: all paths allowed"
    if prefix not in rules or prefix in META_KEYS:
        known = ", ".join(sorted({k for k in rules if k not in META_KEYS} | UNRESTRICTED))
        return False, files, f"branch '{branch}' has no known agent prefix (expected one of: {known})"

    def matches(f: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(f, p) for p in patterns)

    protected = [f for f in files if matches(f, rules.get("protected", []))]
    if protected:
        return False, protected, "only Bhuvanesh ('me/' branch) may change these files (SPEC §4a change rule)"
    patterns = rules[prefix] + rules.get("shared", [])
    bad = [f for f in files if not matches(f, patterns)]
    if bad:
        return False, bad, f"'{prefix}' may not change these files (SPEC §4a, infra/ci/ownership.yml)"
    return True, [], f"'{prefix}': all {len(files)} changed files are within its paths"


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True)
    ap.add_argument("--base", help="git ref to diff against, e.g. origin/dev")
    ap.add_argument("--files", nargs="*")
    args = ap.parse_args()
    files = args.files if args.files is not None else changed_files(args.base)
    ok, bad, msg = check(args.branch, files, load_rules())
    print(msg)
    for f in bad:
        print(f"  not allowed: {f}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
