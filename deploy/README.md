# AWS deployment

The QA agent runs on AWS as a static site plus two Lambda functions. Nothing is
always-on, so an idle month costs cents rather than dollars.

```
Browser
  │
  ▼
CloudFront  E3C9JL0QZ0ZZ3T
  ├── /*        → S3  strands-qa-site-…      index.html, app.js, style.css
  └── /api/*    → HTTP API ae2gy9ebb6 → Lambda strands-qa-api  (512 MB, 30 s)
                                │
                                ├── presigned S3 PUT  → browser uploads direct
                                ├── async invoke      → Lambda strands-qa-worker
                                │                        (3008 MB, 900 s, 2 GB /tmp)
                                │                        Playwright + Tesseract + LLM
                                └── job status        ← S3 strands-qa-data-…
```

Why two functions: a QA run takes minutes, a browser request must be answered in
milliseconds. `POST /api/qa` returns a `job_id` immediately and the UI polls
`GET /api/jobs/{id}`. Uploads and report downloads use presigned S3 URLs, so no
large payload ever crosses Lambda and the 6 MB response limit is never a concern
however many screenshots a report carries.

## Monthly cost

| Service | Driver | Cost |
|---|---|---|
| Lambda | 400,000 GB-s/month always free; a 4-min run at 3 GB ≈ 720 GB-s | $0 up to ~550 runs/month |
| CloudFront | 1 TB egress + 10M requests/month always free | $0 |
| S3 | reports + uploads, lifecycle-expired | ~$0.02 |
| ECR | one 830 MB image, lifecycle keeps 2 | ~$0.08 |
| HTTP API | $1.00 per million requests | ~$0.05 per 1000 runs |
| CloudWatch Logs | 5 GB/month free | $0 |
| SSM Parameter Store | Standard tier | $0 |
| **Total** | | **well under $1/month** |

The OpenRouter API bill is separate and unchanged by this migration.

A `$5/month` AWS Budget (`strands-qa-monthly`) emails at 50% actual and 100%
forecast. The IAM policy also hard-denies EC2/RDS/EKS/ECS/OpenSearch/Lightsail,
so an accidental expensive resource cannot be created with these credentials.

## Why an HTTP API rather than a Lambda function URL

This account blocks public Lambda function URLs at the account level — a
throwaway function created to test it was refused identically, so it is not
specific to this code. CloudFront OAC signing against an IAM-auth function URL
was also rejected. A manually SigV4-signed request to the same function returns
200, so the function was never the problem.

The HTTP API is a `$default` route with a Lambda proxy integration and payload
format 2.0, whose event shape (`rawPath`, `requestContext.http.method`) is
identical to a function URL's — so `handler.api_handler` needed no changes.

The execute-api endpoint is public, so the API is gated in code instead:
CloudFront injects a secret `x-origin-token` header on every origin request and
`handler._from_cloudfront()` compares it in constant time. The token lives in
SSM at `/strands-qa/origin-token`. Calling the execute-api URL directly returns
`{"error": "forbidden"}`.

## Deploying a change

```bash
python deploy/redeploy.py          # code + site
python deploy/redeploy.py --site   # static files only, no image build
```

`redeploy.py` zips `src/`, builds the image in CodeBuild (no local Docker
needed), rolls both Lambdas onto it, syncs the site to S3 and invalidates the
CloudFront cache.

## Secrets

`OPENROUTER_API_KEY` lives in SSM Parameter Store at
`/strands-qa/openrouter-api-key` as a SecureString. It is **not** a Lambda
environment variable — those are readable by anyone with `lambda:GetFunction`.
`handler._load_secrets()` fetches it once per cold start, before any `qa_agent`
module is imported (those snapshot `os.environ` at import time).

To rotate:

```bash
aws ssm put-parameter --name /strands-qa/openrouter-api-key \
  --value "<new key>" --type SecureString --overwrite
```

No redeploy needed; the next cold start picks it up.

## Local development is unchanged

`streamlit run streamlit_app.py` and `python -m qa_agent.web` still work exactly
as before. `app.js` probes for `/api/uploads`: present on AWS (async job flow),
absent locally (the original blocking multipart POST), so one file serves both.

## Tearing it down

```bash
python deploy/teardown.py --dry-run
python deploy/teardown.py --confirm
```

Every target is checked against the `strands-qa-` prefix before deletion.

## Files

| Path | Purpose |
|---|---|
| `deploy/Dockerfile` | Lambda container image: Python 3.12, Chromium, Tesseract |
| `deploy/buildspec.yml` | CodeBuild recipe that builds and pushes it |
| `deploy/package_source.py` | Zips the build context and uploads it to S3 |
| `deploy/redeploy.py` | Ship a change |
| `deploy/teardown.py` | Delete the stack |
| `deploy/stack.json` | Resource names and the live URL |
| `deploy/iam/` | The exact policy documents that were applied |
| `src/qa_agent/cloud/handler.py` | API + worker Lambda entry points |
| `src/qa_agent/cloud/storage.py` | S3 job store and presigned URLs |
