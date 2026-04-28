"""Deterministic pipeline runner — calls the tools directly, no LLM tool-routing.

Useful for CI or when you want predictable execution. The Strands agent in
`agent.py` is the LLM-driven path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .tools.compliance_tool import check_compliance
from .tools.spell_tool import check_spelling
from .tools.template_tool import analyse_template, analyse_template_text
from .tools.web_tools import capture_excerpts, scrape_page

LogFn = Callable[[str], None]

# Phrases the LLM uses when it's emitting a non-issue ("this is fine"). Belt-and-
# braces filter — the prompt also tells the LLM not to do this, but it sometimes does.
_NON_ISSUE_PHRASES = (
    "no change needed",
    "no change required",
    "is correct",
    "spelling is correct",
    "is already correct",
    "consistency check",
    "just confirming",
    "looks fine",
    "looks good",
    "no issue",
    "no fix",
    "no error",
    "is acceptable",
)


def _is_real_issue(issue: dict) -> bool:
    excerpt = (issue.get("excerpt") or "").strip()
    suggestion = (issue.get("suggestion") or "").strip()
    description = (issue.get("description") or "").lower()
    # Excerpt and suggestion identical → nothing to fix.
    if excerpt and suggestion and excerpt.strip("\"'“”‘’") == suggestion.strip("\"'“”‘’"):
        return False
    if any(phrase in description for phrase in _NON_ISSUE_PHRASES):
        return False
    return True


def run_pipeline(
    url: str,
    template_path: str | None = None,
    template_text: str | None = None,
    log: LogFn = print,
) -> dict:
    log(f"[1/5] Scraping {url}")
    page = scrape_page(url)

    template = None
    if template_text:
        log("[2/5] Interpreting template (text)")
        template = analyse_template_text(template_text)
        log(f"      {len(template.get('rules', []))} rules extracted")
    elif template_path:
        log(f"[2/5] Interpreting template image: {template_path}")
        template = analyse_template(template_path)
        log(f"      {len(template.get('rules', []))} rules extracted")
    else:
        log("[2/5] No template supplied — skipping")

    log("[3/5] Running UK English spelling/grammar check")
    spell = check_spelling(page["text"])
    log(f"      {len(spell['issues'])} language issues")

    compliance = {"issues": []}
    if template and template.get("rules"):
        log("[4/5] Running template compliance check")
        compliance = check_compliance(
            page_text=page["text"],
            headings=page.get("headings", []),
            rules=template["rules"],
        )
        log(f"      {len(compliance['issues'])} compliance issues")
    else:
        log("[4/5] Skipping compliance (no rules)")

    raw_issues: list[dict] = []
    raw_issues.extend(spell["issues"])
    raw_issues.extend(compliance["issues"])
    issues = [i for i in raw_issues if _is_real_issue(i)]
    dropped = len(raw_issues) - len(issues)
    if dropped:
        log(f"      filtered out {dropped} non-issue entries (LLM noise)")

    excerpts = [i.get("excerpt", "") for i in issues if i.get("excerpt")]
    log(f"[5/5] Capturing focused evidence screenshots ({len(set(excerpts))} unique excerpts)")
    try:
        evidence = capture_excerpts(url, excerpts) if excerpts else {}
    except Exception as exc:
        log(f"      evidence capture failed: {exc}")
        evidence = {}
    log(f"      captured {len(evidence)}/{len(set(excerpts))} excerpts")

    for issue in issues:
        excerpt = issue.get("excerpt") or ""
        shot = evidence.get(excerpt)
        if shot:
            issue["screenshot"] = shot

    return {
        "course_name": page.get("title") or "Course Page",
        "url": url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "template_summary": (template or {}).get("summary"),
        "issues": issues,
    }
