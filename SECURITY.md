# Security

## Secrets management
- Secrets live in `.env` (gitignored). `.env.example` ships placeholders only.
- `OPENROUTER_API_KEY` is required and validated at startup via `security.require_env`.
- All log output passes through `RedactingFormatter`, which strips known env-var
  values plus heuristic patterns (`sk-...`, `Bearer ...`, long opaque tokens).
- Never commit `.env`, real screenshots of authenticated pages, or generated
  reports that may contain credentials.
- Generate the MCP auth token with cryptographically secure randomness:
  `python -c "import secrets;print(secrets.token_urlsafe(32))"`.

## Network safety (SSRF)
- `security.validate_public_url` runs on every URL fed to the scraper or
  screenshot tool. It rejects:
  - non-`http(s)` schemes
  - bare IPs in private/loopback/link-local/reserved/multicast ranges
  - common metadata hostnames (`localhost`, `*.internal`, `metadata.google.internal`)
- Outbound HTTP from the LLM client uses TLS verification, no automatic redirect
  following, explicit timeouts, and connection-pool caps.

## Filesystem safety
- Template images are resolved through `security.safe_resolve_template`, which
  enforces:
  - allowlisted file extensions
  - allowlisted root directories (project cwd + `./templates`, configurable
    via `QA_TEMPLATE_DIRS`)
  - max file size (`QA_MAX_IMAGE_BYTES`, default 10 MiB)
- Raw base64 / `data:` URIs are intentionally **not** accepted by the template
  tool — keeping the surface to vetted on-disk files.

## MCP server
- Binds to `127.0.0.1` by default. Override with `MCP_HOST=0.0.0.0` only when
  you have an external auth/network policy in front of it.
- Optional bearer-token auth via `MCP_AUTH_TOKEN`. When set, every tool call
  must include `auth_token` matching the env value (compared in constant time).
- Errors raised inside tools are returned as MCP error responses; they are
  redacted before logging.

## Input limits
- Text/HTML payloads sent to the LLM are truncated to `QA_MAX_TEXT_BYTES`
  (default 64 KiB).
- LLM HTTP responses are size-capped via `QA_MAX_HTTP_RESPONSE_BYTES`.

## Dependency hygiene
- All deps in `requirements.txt` are version-pinned with `>=` floors. For
  production, generate a fully pinned lock file with:
  `pip install pip-tools && pip-compile requirements.txt -o requirements.lock`.
- Run `pip-audit` (or `safety check`) periodically against the lock file.

## Reporting a vulnerability
Open a GitHub security advisory (preferred) or email the maintainer. Please do
not file public issues for vulnerabilities.
