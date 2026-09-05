"""One command to ship a code change to AWS.

    python deploy/redeploy.py            # code + site
    python deploy/redeploy.py --site     # static files only (fast, no image build)

Repackages src/, rebuilds the container image in CodeBuild, points both Lambda
functions at the new image, syncs the static site to S3, and invalidates the
CloudFront cache. Everything is named strands-qa-*; nothing else is touched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGION = "eu-west-2"
ACCOUNT = "174976254754"
SITE_BUCKET = f"strands-qa-site-{ACCOUNT}"
PROJECT = "strands-qa-image-build"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/strands-qa-agent:latest"
FUNCTIONS = ("strands-qa-api", "strands-qa-worker")
STATE = Path(__file__).with_name("stack.json")


def aws(*args: str, parse: bool = True):
    out = subprocess.run(["aws", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:3])} failed:\n{out.stderr.strip()}")
    text = out.stdout.strip()
    return json.loads(text) if parse and text else text


def build_image() -> None:
    subprocess.run([sys.executable, str(ROOT / "deploy" / "package_source.py")], check=True)

    build_id = aws("codebuild", "start-build", "--project-name", PROJECT,
                   "--query", "build.id", "--output", "text", parse=False)
    print(f"build started: {build_id}")

    while True:
        time.sleep(15)
        status = aws("codebuild", "batch-get-builds", "--ids", build_id,
                     "--query", "builds[0].buildStatus", "--output", "text", parse=False)
        if status != "IN_PROGRESS":
            break
        print("  building...")
    if status != "SUCCEEDED":
        raise SystemExit(f"image build {status} — see CodeBuild logs for {build_id}")
    print("image built and pushed")

    for fn in FUNCTIONS:
        aws("lambda", "update-function-code", "--function-name", fn,
            "--image-uri", IMAGE, "--query", "FunctionName", "--output", "text", parse=False)
        print(f"  {fn} -> new image")
    for fn in FUNCTIONS:
        # update-function-code returns immediately; the function is not callable
        # again until the new image finishes deploying.
        aws("lambda", "wait", "function-updated-v2", "--function-name", fn, parse=False)
        print(f"  {fn} ready")


def sync_site() -> None:
    static = ROOT / "src" / "qa_agent" / "static"
    uploads = [
        (static / "index.html", "index.html", "text/html; charset=utf-8", "no-cache"),
        (static / "app.js", "static/app.js", "application/javascript; charset=utf-8", "public,max-age=300"),
        (static / "style.css", "static/style.css", "text/css; charset=utf-8", "public,max-age=300"),
    ]
    for src, key, ctype, cache in uploads:
        aws("s3", "cp", str(src), f"s3://{SITE_BUCKET}/{key}",
            "--content-type", ctype, "--cache-control", cache, parse=False)
        print(f"  uploaded {key}")

    if STATE.exists():
        dist = json.loads(STATE.read_text())["distribution_id"]
        aws("cloudfront", "create-invalidation", "--distribution-id", dist,
            "--paths", "/*", "--query", "Invalidation.Id", "--output", "text", parse=False)
        print("cloudfront cache invalidated")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", action="store_true",
                    help="only sync the static site; skip the image build")
    args = ap.parse_args()

    if not args.site:
        build_image()
    sync_site()

    if STATE.exists():
        print(f"\nlive at {json.loads(STATE.read_text())['url']}")


if __name__ == "__main__":
    main()
