from ..llm_client import call_llm_json

SYSTEM = (
    "You are a meticulous UK English copy editor reviewing course web page content. "
    "Use UK English spelling (colour, organisation, analyse, behaviour, programme). "
    "Flag every spelling mistake, grammar error, punctuation issue, inconsistent "
    "tense, or awkward phrasing. Do NOT accept US spellings — flag them and suggest "
    "the UK form. Ignore navigation labels, cookie notices, and footer boilerplate.\n\n"
    "STRICT FILTER — only emit an issue if there is a real, fixable problem:\n"
    "  * NEVER emit an issue whose description says 'is correct', 'no change "
    "    needed', 'just confirming', 'consistency check', 'looks fine', or similar.\n"
    "  * NEVER emit issues where excerpt and suggestion are the same string.\n"
    "  * If the text is already correct UK English, return {\"issues\": []}."
)

SCHEMA_INSTRUCTION = """Return a JSON object with this exact shape:
{
  "issues": [
    {
      "type": "Spelling" | "Grammar" | "Punctuation" | "Style",
      "severity": "Critical" | "Minor" | "Info",
      "excerpt": "<the offending text, kept short>",
      "description": "<what is wrong>",
      "suggestion": "<the corrected text>"
    }
  ]
}
If there are no issues, return {"issues": []}. Output ONLY the JSON object."""


def check_spelling(text: str) -> dict:
    trimmed = (text or "")[:12000]
    prompt = f'{SCHEMA_INSTRUCTION}\n\nTEXT TO REVIEW:\n"""{trimmed}"""'
    result = call_llm_json(prompt, system=SYSTEM)
    issues = result.get("issues") or []
    return {"issues": issues if isinstance(issues, list) else []}
