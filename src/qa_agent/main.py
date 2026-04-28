"""CLI entry point.

Default mode is the **deterministic pipeline** — exactly 3 LLM calls
(template → spell → compliance) and no agent-driven retries. Use ``--agent``
to opt into the LLM-orchestrated Strands agent (more flexible, more tokens).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from dotenv import load_dotenv

from .logging_config import configure_logging
from .pipeline import run_pipeline
from .security import redact
from .tools.report_tool import generate_pdf

load_dotenv()
configure_logging()


@click.command()
@click.option("--url", "-u", required=True, help="Course page URL to QA.")
@click.option("--template", "-t", "template_path", default=None, help="Path to a QA template image.")
@click.option("--template-text", default=None, help="Inline QA template text (alternative to --template).")
@click.option("--out", "-o", default=None, help="Output PDF path (defaults to reports/qa-report-<ts>.pdf).")
@click.option("--agent", "use_agent", is_flag=True, default=False,
              help="Use the LLM-orchestrated Strands agent (more LLM calls). "
                   "Default is the deterministic pipeline.")
@click.option("--auto-fallback", is_flag=True, default=False,
              help="If --agent fails to return JSON, also run the pipeline. "
                   "Off by default (running both costs LLM calls twice).")
def main(url: str, template_path: str | None, template_text: str | None,
         out: str | None, use_agent: bool, auto_fallback: bool) -> None:
    if use_agent:
        report = _run_with_agent(
            url=url,
            template_path=template_path,
            template_text=template_text,
            auto_fallback=auto_fallback,
        )
    else:
        report = run_pipeline(url=url, template_path=template_path, template_text=template_text)

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = reports_dir / f"qa-report-{stamp}.json"
    pdf_path = Path(out) if out else reports_dir / f"qa-report-{stamp}.pdf"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    generate_pdf(report, str(pdf_path))

    click.echo("\nDone.")
    click.echo(f"  JSON: {json_path}")
    click.echo(f"  PDF:  {pdf_path}")
    click.echo(f"  Issues: {len(report.get('issues', []))}")


def _run_with_agent(url: str, template_path: str | None, template_text: str | None,
                    auto_fallback: bool) -> dict:
    from .agent import build_agent, build_user_prompt

    with build_agent() as (agent, _client):
        prompt = build_user_prompt(url, template_path, template_text)
        click.echo("Running Strands agent (use --pipeline / default for fewer LLM calls)...")
        result = agent(prompt)

    text = str(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        if auto_fallback:
            click.echo("Agent did not return JSON; --auto-fallback set, running pipeline.", err=True)
            return run_pipeline(url=url, template_path=template_path, template_text=template_text)
        click.echo(
            "Agent did not return JSON. Skipping pipeline fallback to save LLM calls.\n"
            "Re-run with the default (pipeline) mode, or pass --auto-fallback to retry automatically.",
            err=True,
        )
        return {
            "course_name": "QA run incomplete",
            "url": url,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "template_summary": None,
            "issues": [],
            "raw_agent_output": text,
        }


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        click.echo(f"\nQA run failed: {redact(str(exc))}", err=True)
        sys.exit(1)
