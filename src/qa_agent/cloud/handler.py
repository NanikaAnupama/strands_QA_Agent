"""Lambda entry points for the QA agent.

Two handlers share one container image:

``api_handler``     Lambda Function URL, fronted by CloudFront at ``/api/*``.
                    Small, fast, 512 MB. Validates input, hands out presigned
                    S3 URLs, starts jobs, reports status.
``worker_handler``  Invoked asynchronously by the API. Runs the real pipeline
                    (Playwright + Tesseract + LLM), writes JSON + PDF to S3.

The split exists because a QA run takes minutes while a browser request must
be answered in milliseconds. The API never blocks on a run — it returns a
``job_id`` and the UI polls ``GET /api/jobs/{id}``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import boto3

# qa_agent modules resolve these at import time, and /var/task is read-only on
# Lambda, so every writable path must point at /tmp before anything is imported.
os.environ.setdefault("QA_EXTRACTION_DIR", "/tmp/extraction")
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/cache")


def _load_secrets() -> None:
    """Pull SecureString parameters into the environment at cold start.

    Lambda cannot expand an SSM SecureString into an environment variable, and
    the key must not sit in plaintext in the function's configuration where
    anyone with lambda:GetFunction can read it. So we fetch it here — before the
    qa_agent modules below, which snapshot os.environ at import time. One
    GetParameter call per cold start; warm invocations reuse the value.
    """
    prefix = os.environ.get("QA_SSM_PREFIX", "/strands-qa")
    wanted = {
        f"{prefix}/openrouter-api-key": "OPENROUTER_API_KEY",
        f"{prefix}/origin-token": "QA_ORIGIN_TOKEN",
    }
    missing = [p for p, var in wanted.items() if not os.environ.get(var)]
    if not missing:
        return
    try:
        ssm = boto3.client("ssm")
        got = ssm.get_parameters(Names=missing, WithDecryption=True)
        for param in got.get("Parameters", []):
            os.environ[wanted[param["Name"]]] = param["Value"]
        for name in got.get("InvalidParameters", []):
            logging.getLogger(__name__).error("missing SSM parameter %s", name)
    except Exception:
        # Logged, not raised: the pipeline reports a clean tool_failure if the
        # key is absent, which is far more useful than a cold-start crash.
        logging.getLogger(__name__).exception("could not load SSM parameters")


_load_secrets()

from qa_agent.logging_config import configure_logging  # noqa: E402
from qa_agent.cloud import storage  # noqa: E402
from qa_agent.security import (  # noqa: E402
    ALLOWED_DOC_SUFFIXES,
    ALLOWED_TEMPLATE_SUFFIXES,
    MAX_DOC_BYTES,
    MAX_IMAGE_BYTES,
    redact,
    validate_public_url,
)

configure_logging()
logger = logging.getLogger(__name__)

WORKER_FUNCTION = os.environ.get("QA_WORKER_FUNCTION", "")
MAX_URL_LEN = 2048
MAX_TEMPLATE_TEXT_LEN = 200_000

_lambda = boto3.client("lambda")

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".gif": "image/gif",
}

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


# --- HTTP plumbing ----------------------------------------------------------

def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }


def _bad(msg: str, status: int = 400) -> dict:
    return _resp(status, {"error": msg})


def _route(event: dict) -> tuple[str, str]:
    ctx = event.get("requestContext", {}).get("http", {})
    return ctx.get("method", "GET").upper(), event.get("rawPath", "/")


def _json_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}")
    if not isinstance(parsed, dict):
        raise ValueError("body must be a JSON object")
    return parsed


# --- API handler ------------------------------------------------------------

def _from_cloudfront(event: dict) -> bool:
    """True when the request carries the secret CloudFront injects at the origin.

    The function URL is reachable on the open internet, so this header is what
    actually gates it: CloudFront adds it to every origin request, and a caller
    who guesses the URL cannot guess this. Compared in constant time.
    """
    expected = os.environ.get("QA_ORIGIN_TOKEN", "")
    if not expected:
        # No token configured — fail closed rather than silently serving everyone.
        logger.error("QA_ORIGIN_TOKEN is not set; refusing every request")
        return False
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    import hmac

    return hmac.compare_digest(headers.get("x-origin-token", ""), expected)


def api_handler(event: dict, _context) -> dict:
    method, path = _route(event)

    if not _from_cloudfront(event):
        return _bad("forbidden", 403)

    if path == "/api/health":
        return _resp(200, {
            "ok": True,
            "mode": "pipeline",
            "model": os.environ.get("MODEL", "(unset)"),
            "vision_model": os.environ.get("QA_VISION_MODEL") or None,
            "region": os.environ.get("AWS_REGION", ""),
        })

    if method == "POST" and path == "/api/uploads":
        return _presign_uploads(event)

    if method == "POST" and path == "/api/qa":
        return _start_job(event)

    m = re.fullmatch(r"/api/jobs/([0-9a-f]{32})", path)
    if method == "GET" and m:
        return _job_status(m.group(1))

    return _bad("not found", 404)


def _presign_uploads(event: dict) -> dict:
    """Mint presigned PUT URLs so the browser uploads straight to S3.

    Called before /api/qa. The returned keys are what the caller then passes as
    `template_key` / `spec_key`.
    """
    try:
        body = _json_body(event)
    except ValueError as exc:
        return _bad(str(exc))

    job_id = uuid4().hex
    out: dict[str, dict[str, str]] = {}
    for slot in ("template", "spec"):
        filename = (body.get(f"{slot}_filename") or "").strip()
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_TEMPLATE_SUFFIXES:
            return _bad(f"unsupported {slot} file type: {suffix or '(none)'}")
        size = int(body.get(f"{slot}_size") or 0)
        cap = MAX_DOC_BYTES if suffix in ALLOWED_DOC_SUFFIXES else MAX_IMAGE_BYTES
        if size > cap:
            return _bad(f"{slot} file exceeds the {cap // (1024 * 1024)} MB limit", 413)
        key = f"uploads/{job_id}/{slot}{suffix}"
        content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
        out[slot] = {
            "key": key,
            "url": storage.presign_upload(key, content_type),
            "content_type": content_type,
        }

    return _resp(200, {"job_id": job_id, "uploads": out})


def _start_job(event: dict) -> dict:
    try:
        body = _json_body(event)
    except ValueError as exc:
        return _bad(str(exc))

    url = (body.get("url") or "").strip()
    if not url:
        return _bad("field `url` is required")
    if len(url) > MAX_URL_LEN:
        return _bad("url is too long")
    try:
        url = validate_public_url(url)
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim to the caller
        return _bad(f"URL rejected: {exc}")

    reference_url = (body.get("reference_url") or "").strip() or None
    if reference_url:
        if len(reference_url) > MAX_URL_LEN:
            return _bad("reference url is too long")
        try:
            reference_url = validate_public_url(reference_url)
        except Exception as exc:  # noqa: BLE001
            return _bad(f"Reference URL rejected: {exc}")

    template_text = (body.get("template_text") or "").strip() or None
    if template_text and len(template_text) > MAX_TEMPLATE_TEXT_LEN:
        return _bad("template_text exceeds size limit")

    job_id = (body.get("job_id") or "").strip() or uuid4().hex
    if not _JOB_ID.fullmatch(job_id):
        return _bad("invalid job_id")

    # Only accept keys that live under this job's own upload prefix, so a caller
    # can't point the worker at somebody else's object.
    def _own_key(value) -> str | None:
        key = (value or "").strip()
        if not key:
            return None
        if not key.startswith(f"uploads/{job_id}/"):
            return None
        return key if storage.object_exists(key) else None

    payload = {
        "job_id": job_id,
        "url": url,
        "reference_url": reference_url,
        "template_text": template_text,
        "template_key": _own_key(body.get("template_key")),
        "spec_key": _own_key(body.get("spec_key")),
    }

    storage.put_job(job_id, {
        "status": "queued",
        "url": url,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    _lambda.invoke(
        FunctionName=WORKER_FUNCTION,
        InvocationType="Event",  # fire-and-forget; the worker updates S3
        Payload=json.dumps(payload).encode("utf-8"),
    )

    return _resp(202, {"job_id": job_id, "status": "queued"})


def _job_status(job_id: str) -> dict:
    doc = storage.get_job(job_id)
    if doc is None:
        return _bad("unknown job", 404)

    out = {k: doc.get(k) for k in ("job_id", "status", "url", "created_at",
                                   "updated_at", "error", "course_name",
                                   "issue_counts")}
    # Presigned at read time so a link is always fresh when the UI receives it.
    if doc.get("status") == "done":
        if doc.get("json_key"):
            out["report_url"] = storage.presign_inline(doc["json_key"])
            out["json_url"] = storage.presign_download(
                doc["json_key"], Path(doc["json_key"]).name)
        if doc.get("pdf_key"):
            out["pdf_url"] = storage.presign_download(
                doc["pdf_key"], Path(doc["pdf_key"]).name)
        out["pdf_error"] = doc.get("pdf_error")
    return _resp(200, out)


# --- worker handler ---------------------------------------------------------

def worker_handler(event: dict, _context) -> dict:
    """Run the pipeline for one job and publish its artefacts to S3."""
    job_id = event["job_id"]
    url = event["url"]
    storage.patch_job(job_id, status="running")

    tmp = Path(tempfile.mkdtemp(prefix=f"qa-{job_id}-", dir="/tmp"))
    try:
        template_path = _fetch(event.get("template_key"), tmp)
        spec_path = _fetch(event.get("spec_key"), tmp)

        from qa_agent.pipeline import run_qa_pipeline
        from qa_agent.tools.report_tool import generate_pdf
        from qa_agent.tools.web_tools import (
            EVIDENCE_TOKEN_PREFIX,
            attach_issue_screenshots,
            read_evidence_png,
        )

        report = run_qa_pipeline(
            url,
            template_path,
            event.get("template_text"),
            spec_path,
            reference_url=event.get("reference_url"),
        )
        report.pop("reasoning", None)
        report.setdefault("url", url)
        attach_issue_screenshots(report)

        # Swap evidence:// tokens for inline base64 so the report is self-contained.
        for issue in report.get("issues", []) or []:
            token = issue.get("screenshot")
            if isinstance(token, str) and token.startswith(EVIDENCE_TOKEN_PREFIX):
                b64 = read_evidence_png(token)
                if b64:
                    issue["screenshot"] = b64
                else:
                    issue.pop("screenshot", None)

        stamp = storage.utc_stamp()
        json_name = f"qa-report-{stamp}.json"
        pdf_name = f"qa-report-{stamp}.pdf"
        json_path = tmp / json_name
        pdf_path = tmp / pdf_name

        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        json_key = storage.upload_report(str(json_path), json_name, "application/json")

        pdf_key = None
        pdf_error = None
        try:
            generate_pdf(report, str(pdf_path))
            pdf_key = storage.upload_report(str(pdf_path), pdf_name, "application/pdf")
        except Exception as exc:  # noqa: BLE001 — a missing PDF must not lose the run
            logger.exception("PDF generation failed")
            pdf_error = redact(str(exc))

        counts: dict[str, int] = {}
        for issue in report.get("issues", []) or []:
            sev = issue.get("severity", "Info")
            counts[sev] = counts.get(sev, 0) + 1

        storage.patch_job(
            job_id,
            status="done",
            course_name=report.get("course_name"),
            issue_counts=counts,
            json_key=json_key,
            pdf_key=pdf_key,
            pdf_error=pdf_error,
        )
        return {"ok": True, "job_id": job_id}

    except Exception as exc:  # noqa: BLE001 — the UI must always learn the outcome
        logger.exception("job %s failed", job_id)
        storage.patch_job(
            job_id, status="error",
            error=f"{type(exc).__name__}: {redact(str(exc))}",
        )
        return {"ok": False, "job_id": job_id}
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _fetch(key: str | None, tmp: Path) -> str | None:
    if not key:
        return None
    dest = tmp / Path(key).name
    storage.download_to(key, str(dest))
    return str(dest)
