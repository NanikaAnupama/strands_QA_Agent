"""MCP server exposing the QA tools over streamable-HTTP transport.

Run with: python -m qa_agent.mcp_server

Security defaults:
  * Binds to 127.0.0.1 (override with MCP_HOST=0.0.0.0 if you really need it).
  * Optional bearer-token auth via MCP_AUTH_TOKEN.
  * URL/path inputs are validated by the underlying tool implementations.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .logging_config import configure_logging
from .security import constant_time_equals, redact
from .tools.compliance_tool import check_compliance
from .tools.spell_tool import check_spelling
from .tools.template_tool import analyse_template, analyse_template_text
from .tools.web_tools import capture_excerpts, scrape_page, take_screenshot

load_dotenv()
configure_logging()
logger = logging.getLogger(__name__)

_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
_PORT = int(os.environ.get("MCP_PORT", "3001"))
_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()

mcp = FastMCP("QA Tools", host=_HOST, port=_PORT)


def _check_auth(token: str | None) -> None:
    """If MCP_AUTH_TOKEN is configured, every tool call must present it."""
    if not _AUTH_TOKEN:
        return
    if not token or not constant_time_equals(token, _AUTH_TOKEN):
        raise PermissionError("Invalid or missing auth token.")


@mcp.tool()
def scrape(url: str, auth_token: str | None = None) -> dict:
    """Fetch a web page and return its title, body text, headings, links, and images."""
    _check_auth(auth_token)
    return scrape_page(url)


@mcp.tool()
def screenshot(url: str, selector: str | None = None, full_page: bool = True,
               auth_token: str | None = None) -> dict:
    """Capture a base64-encoded PNG of a page (or a specific CSS selector)."""
    _check_auth(auth_token)
    return {"img": take_screenshot(url, selector=selector, full_page=full_page)}


@mcp.tool()
def evidence(url: str, excerpts: list[str], auth_token: str | None = None) -> dict:
    """Open `url` once and return focused per-excerpt screenshots.

    Returns {"shots": {excerpt: base64_png, ...}}. Excerpts whose element
    cannot be located on the page are silently omitted.
    """
    _check_auth(auth_token)
    return {"shots": capture_excerpts(url, excerpts)}


@mcp.tool()
def spell(text: str, auth_token: str | None = None) -> dict:
    """Run a UK English spelling/grammar check and return structured issues."""
    _check_auth(auth_token)
    return check_spelling(text)


@mcp.tool()
def template(image_path: str | None = None, text: str | None = None,
             auth_token: str | None = None) -> dict:
    """Interpret a QA template (image via OCR, or raw text) into a rule list."""
    _check_auth(auth_token)
    if text:
        return analyse_template_text(text)
    if not image_path:
        raise ValueError("Provide either `image_path` or `text`.")
    return analyse_template(image_path)


@mcp.tool()
def compliance(page_text: str, headings: list, rules: list,
               auth_token: str | None = None) -> dict:
    """Audit page text + headings against a list of QA template rules."""
    _check_auth(auth_token)
    return check_compliance(page_text=page_text, headings=headings, rules=rules)


if __name__ == "__main__":
    auth_state = "ENABLED" if _AUTH_TOKEN else "DISABLED (set MCP_AUTH_TOKEN to enable)"
    logger.info("MCP Server starting on http://%s:%s/mcp (auth: %s)", _HOST, _PORT, auth_state)
    try:
        mcp.run(transport="streamable-http")
    except Exception as exc:
        logger.error("MCP server crashed: %s", redact(str(exc)))
        raise
