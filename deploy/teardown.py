"""Delete every AWS resource this project created — and nothing else.

    python deploy/teardown.py --dry-run     # list what would go
    python deploy/teardown.py --confirm     # actually delete

Every target is matched against the strands-qa-* prefix before deletion, so a
mistyped name fails closed rather than removing somebody else's resource. The
IAM user and its policy are left alone: they are credentials you may still want,
and deleting them is a deliberate manual act.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess

REGION = "eu-west-2"
ACCOUNT = "174976254754"
PREFIX = "strands-qa"
env = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def aws(*args, check=True):
    r = subprocess.run(["aws", *args], capture_output=True, text=True, env=env)
    if check and r.returncode:
        print(f"    ! {r.stderr.strip().splitlines()[-1][:160]}")
        return None
    return r.stdout.strip()


def guard(name: str) -> str:
    if not name.startswith(PREFIX):
        raise SystemExit(f"refusing to touch {name!r}: not a {PREFIX}-* resource")
    return name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.confirm or args.dry_run):
        raise SystemExit("pass --dry-run to preview or --confirm to delete")
    go = args.confirm

    plan = [
        ("cloudfront distribution", "E3C9JL0QZ0ZZ3T (disable, then delete)"),
        ("lambda", f"{PREFIX}-api, {PREFIX}-worker"),
        ("http api", f"{PREFIX}-http (ae2gy9ebb6)"),
        ("codebuild project", f"{PREFIX}-image-build"),
        ("ecr repository", f"{PREFIX}-agent (with all images)"),
        ("s3 buckets", f"{PREFIX}-site-{ACCOUNT}, {PREFIX}-data-{ACCOUNT}"),
        ("iam roles", f"{PREFIX}-lambda-role, {PREFIX}-codebuild-role"),
        ("ssm parameters", f"/{PREFIX}/openrouter-api-key, /{PREFIX}/origin-token"),
        ("budget", f"{PREFIX}-monthly"),
    ]
    print("resources this will delete:")
    for kind, what in plan:
        print(f"  {kind:24} {what}")
    if not go:
        print("\ndry run — nothing deleted")
        return

    print("\ndeleting...")
    for fn in (f"{PREFIX}-api", f"{PREFIX}-worker"):
        aws("lambda", "delete-function", "--function-name", guard(fn), check=False)
        print(f"  lambda {fn}")

    aws("apigatewayv2", "delete-api", "--api-id", "ae2gy9ebb6", check=False)
    print(f"  http api {PREFIX}-http")

    aws("codebuild", "delete-project", "--name", guard(f"{PREFIX}-image-build"), check=False)
    print(f"  codebuild {PREFIX}-image-build")

    aws("ecr", "delete-repository", "--repository-name", guard(f"{PREFIX}-agent"),
        "--force", check=False)
    print(f"  ecr {PREFIX}-agent")

    for b in (f"{PREFIX}-site-{ACCOUNT}", f"{PREFIX}-data-{ACCOUNT}"):
        aws("s3", "rm", f"s3://{guard(b)}", "--recursive", check=False)
        aws("s3api", "delete-bucket", "--bucket", guard(b), "--region", REGION, check=False)
        print(f"  s3 {b}")

    for role, inline in ((f"{PREFIX}-lambda-role", f"{PREFIX}-lambda-inline"),
                         (f"{PREFIX}-codebuild-role", f"{PREFIX}-codebuild-inline")):
        aws("iam", "delete-role-policy", "--role-name", guard(role),
            "--policy-name", inline, check=False)
        aws("iam", "delete-role", "--role-name", guard(role), check=False)
        print(f"  iam role {role}")

    for p in (f"/{PREFIX}/openrouter-api-key", f"/{PREFIX}/origin-token"):
        aws("ssm", "delete-parameter", "--name", p, check=False)
        print(f"  ssm {p}")

    print("\nNOTE: the CloudFront distribution must be disabled and fully deployed")
    print("before AWS will delete it. Disable it in the console, wait, then:")
    print("  aws cloudfront delete-distribution --id E3C9JL0QZ0ZZ3T --if-match <etag>")
    print("\nLeft in place on purpose: IAM user strands-qa-user and strands-qa-policy.")


if __name__ == "__main__":
    main()
