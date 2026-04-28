"""Strands Agent that connects to the QA MCP server and orchestrates a QA run."""

from __future__ import annotations

import os
from contextlib import contextmanager

from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

from .llm import build_model

SYSTEM_PROMPT = """You are a course-page QA agent. Be silent and efficient.

Strict rules — these exist because every LLM call costs money:

  * DO NOT narrate your plan, your reasoning, or your progress between tool calls.
  * Call each tool AT MOST ONCE per run. If a tool returns an error, do NOT
    retry it with the same arguments. Record the failure in the JSON and move on.
  * Use exactly this fixed order — skip a step if its inputs are missing:
      1. `scrape(url)`
      2. `template(...)`        — only if a template image path or text was provided
      3. `spell(text)`          — pass the scraped page text
      4. `compliance(...)`      — only if step 2 returned rules
      5. `evidence(url, [...])` — pass the list of `excerpt` strings collected
                                  from the spell + compliance issues. This
                                  returns focused per-issue screenshots; merge
                                  each `shots[excerpt]` into the corresponding
                                  issue's `screenshot` field.
        DO NOT call `screenshot` (the full-page tool) — it is wasteful here.
  * After step 5 (or earlier if scrape failed), output ONE JSON object and STOP.
    Do not write any prose before or after the JSON.

JSON schema:
  {
    "course_name": "<page title or 'QA run incomplete'>",
    "url": "<the input url>",
    "template_summary": "<from template tool, or null>",
    "issues": [
      {
        "type": "Spelling|Grammar|Punctuation|Style|Template",
        "severity": "Critical|Minor|Info",
        "ruleId": "<from compliance, else omit>",
        "excerpt": "<short quote>",
        "description": "<what is wrong>",
        "suggestion": "<how to fix>",
        "screenshot": "<base64 or omit>"
      }
    ],
    "tool_failures": ["<tool_name: short reason>", ...]
  }

If MCP tools require an auth token, pass it as the `auth_token` argument.
Use UK English in all descriptions. Do not invent issues."""


def _client_factory(url: str):
    headers: dict[str, str] = {}
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def factory():
        # streamable HTTP client signature changed across mcp versions; pass
        # headers when supported, fall back gracefully.
        try:
            return streamablehttp_client(url, headers=headers or None)
        except TypeError:
            return streamablehttp_client(url)

    return factory


@contextmanager
def build_agent(mcp_url: str | None = None):
    url = mcp_url or os.environ.get("MCP_URL", "http://localhost:3001/mcp")
    client = MCPClient(_client_factory(url))
    with client:
        tools = client.list_tools_sync()
        agent = Agent(model=build_model(), tools=tools, system_prompt=SYSTEM_PROMPT)
        yield agent, client


def build_user_prompt(url: str, template_path: str | None, template_text: str | None) -> str:
    parts = [f"Course URL: {url}"]
    if template_path:
        parts.append(f"QA template image path: {template_path}")
    if template_text:
        parts.append(f"QA template text:\n{template_text}")
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if token:
        parts.append("All tool calls must include `auth_token` set to the configured MCP token.")
    parts.append("Run the full QA flow now and return the JSON report.")
    return "\n\n".join(parts)
