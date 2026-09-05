"""Zip the build context for CodeBuild and upload it to S3.

Only what the image actually needs goes in: the Dockerfile, buildspec,
requirements, and src/. Reports, the virtualenv, caches and .env are excluded —
the .env in particular must never leave the machine (its key already lives in
SSM Parameter Store).
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "strands-qa-data-174976254754"
KEY = "build/source.zip"
OUT = ROOT / "deploy" / ".source.zip"

INCLUDE_FILES = ["requirements.txt", "deploy/Dockerfile", "deploy/buildspec.yml"]
INCLUDE_TREES = ["src"]
SKIP_DIRS = {"__pycache__", ".venv", ".git", "node_modules", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}


def _add_tree(zf: zipfile.ZipFile, tree: str) -> int:
    count = 0
    for path in (ROOT / tree).rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        zf.write(path, path.relative_to(ROOT).as_posix())
        count += 1
    return count


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in INCLUDE_FILES:
            zf.write(ROOT / rel, rel)
            total += 1
        for tree in INCLUDE_TREES:
            total += _add_tree(zf, tree)

    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"packaged {total} files -> {OUT.name} ({size_mb:.2f} MB)")

    subprocess.run(
        ["aws", "s3", "cp", str(OUT), f"s3://{BUCKET}/{KEY}"],
        check=True, stdout=subprocess.DEVNULL,
    )
    print(f"uploaded to s3://{BUCKET}/{KEY}")


if __name__ == "__main__":
    sys.exit(main())
