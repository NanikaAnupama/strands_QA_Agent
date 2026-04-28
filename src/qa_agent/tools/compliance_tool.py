from __future__ import annotations

import json

from ..llm_client import call_llm_json

SYSTEM = (
    "You audit a course web page against a list of QA template rules. "
    "Be strict but factual: only flag a rule as failing if the page text or "
    "structure clearly violates it."
)

SCHEMA_INSTRUCTION = """Return a JSON object:
{
  "issues": [
    {
      "ruleId": "<rule id from the template>",
      "type": "Template",
      "severity": "Critical" | "Minor" | "Info",
      "description": "<what is wrong on the page>",
      "suggestion": "<how to fix it>",
      "excerpt": "<short quote from the page that shows the problem, or empty>"
    }
  ]
}
Output ONLY the JSON object. If the page passes every rule, return {"issues": []}."""


def check_compliance(page_text: str, headings: list, rules: list) -> dict:
    if not rules:
        return {"issues": []}
    compact_headings = "\n".join(f"{h.get('tag', '').upper()}: {h.get('text', '')}" for h in (headings or []))
    prompt = (
        f"{SCHEMA_INSTRUCTION}\n\n"
        f"TEMPLATE RULES:\n{json.dumps(rules, indent=2)}\n\n"
        f"PAGE HEADINGS:\n{compact_headings or '(none)'}\n\n"
        f'PAGE TEXT (truncated):\n"""{(page_text or "")[:8000]}"""'
    )
    result = call_llm_json(prompt, system=SYSTEM)
    issues = result.get("issues") or []
    return {"issues": issues if isinstance(issues, list) else []}
