"""S3-backed artefact + job store for the Lambda deployment.

Layout inside ``QA_DATA_BUCKET``::

    uploads/{job_id}/{template|spec}{suffix}   caller-supplied documents
    jobs/{job_id}.json                         job status document
    reports/qa-report-{stamp}.{json,pdf}       finished artefacts

The API function only ever writes job documents and hands out presigned URLs;
the worker function writes the artefacts. Nothing large is ever passed through
a Lambda response body, which keeps every payload well under the 6 MB Function
URL limit no matter how many screenshots a report carries.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

BUCKET = os.environ.get("QA_DATA_BUCKET", "")
# Presigned links outlive a slow QA run plus a leisurely download.
PRESIGN_TTL = int(os.environ.get("QA_PRESIGN_TTL", "3600"))

# SigV4 is required for presigned URLs to validate in every region.
_s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


def _key_job(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --- job documents ----------------------------------------------------------

def put_job(job_id: str, doc: dict[str, Any]) -> None:
    doc = {**doc, "job_id": job_id, "updated_at": datetime.now(timezone.utc).isoformat()}
    _s3.put_object(
        Bucket=BUCKET,
        Key=_key_job(job_id),
        Body=json.dumps(doc).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-store",
    )


def get_job(job_id: str) -> dict[str, Any] | None:
    try:
        obj = _s3.get_object(Bucket=BUCKET, Key=_key_job(job_id))
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404", "AccessDenied"):
            return None
        raise
    return json.loads(obj["Body"].read())


def patch_job(job_id: str, **fields: Any) -> dict[str, Any]:
    doc = get_job(job_id) or {"job_id": job_id}
    doc.update(fields)
    put_job(job_id, doc)
    return doc


# --- uploads ----------------------------------------------------------------

def presign_upload(key: str, content_type: str) -> str:
    """A PUT URL the browser uses to send a template/spec straight to S3.

    Uploads bypass Lambda entirely, so a 20 MB PDF is no harder than a 20 KB
    one and the API function never sees the bytes.
    """
    return _s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=PRESIGN_TTL,
    )


def download_to(key: str, dest: str) -> str:
    _s3.download_file(BUCKET, key, dest)
    return dest


def object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError:
        return False


# --- artefacts --------------------------------------------------------------

def upload_report(local_path: str, name: str, content_type: str) -> str:
    key = f"reports/{name}"
    _s3.upload_file(local_path, BUCKET, key, ExtraArgs={"ContentType": content_type})
    return key


def presign_download(key: str, filename: str | None = None) -> str:
    params: dict[str, Any] = {"Bucket": BUCKET, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _s3.generate_presigned_url("get_object", Params=params, ExpiresIn=PRESIGN_TTL)


def presign_inline(key: str) -> str:
    """Presigned GET without a download disposition — the UI fetches and renders it."""
    return _s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=PRESIGN_TTL
    )
