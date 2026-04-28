# AI QA Agent for Course Page Quality Assurance

## Overview
This guide explains how to build an AI-powered QA agent that:
- Detects spelling, grammar, and content issues (UK English)
- Understands QA templates (including image-based templates)
- Visits and analyses course web pages
- Captures screenshots of issues
- Generates a structured QA report
- Exports the report as a PDF

The solution uses:
- **Strands Agents SDK** (Python — the official AWS SDK at https://strandsagents.com)
- **OpenRouter** for the LLM (DeepSeek by default, OpenAI-compatible endpoint)
- **MCP server** built with `FastMCP` to expose the QA tools
- **Custom Python tools** (Playwright, Tesseract OCR, PDFKit-equivalent ReportLab)
- **Local execution environment**

> **Language note:** the Strands Agents SDK is Python-only. The earlier JavaScript
> sketch in older versions of this guide does not match a real npm package — this
> guide and the project around it are now fully Python.

---

## 1. Prerequisites

### Python
Use Python 3.11+.

### Install Python dependencies
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

`requirements.txt` (see project root) installs:
- `strands-agents` — the Strands Agents SDK
- `strands-agents-tools` — community tools (optional helpers)
- `mcp` — Model Context Protocol Python SDK (server + client)
- `playwright` — headless browser
- `pytesseract`, `Pillow` — OCR for image-based templates
- `reportlab` — PDF generation
- `python-dotenv`, `httpx`, `openai`, `click`

### Install Playwright's Chromium
```bash
playwright install chromium
```

### Install Tesseract (system binary)
- **Windows:** install from https://github.com/UB-Mannheim/tesseract/wiki and ensure
  `tesseract.exe` is on `PATH` (or set `TESSERACT_CMD` in `.env`).
- **macOS:** `brew install tesseract`
- **Linux (Debian/Ubuntu):** `sudo apt-get install tesseract-ocr`

---

## 2. Architecture Overview

```
User Input (Course URL [+ optional template image])
        │
        ▼
   Strands Agent  ◄── OpenRouter (DeepSeek, OpenAI-compatible API)
        │
        ▼
   MCP Client  ──►  MCP Server (FastMCP, streamable-http on :3001)
        │
        ▼
  ┌─────────────────────── Tools ───────────────────────┐
  │  scrape_page         (Playwright)                    │
  │  take_screenshot     (Playwright)                    │
  │  check_spelling      (LLM, UK English prompt)        │
  │  analyse_template    (Tesseract OCR + LLM)           │
  │  check_compliance    (LLM, page vs rules)            │
  └──────────────────────────────────────────────────────┘
        │
        ▼
  Structured QA report (JSON) → ReportLab → PDF
```

---

## 3. Configure OpenRouter (DeepSeek)

Create `.env` from `.env.example`:
```env
OPENROUTER_API_KEY=your_api_key_here
MODEL=deepseek/deepseek-chat
MCP_PORT=3001
TESSERACT_CMD=
```

Strands talks to OpenRouter through its OpenAI-compatible model provider:

```python
# src/qa_agent/llm.py
import os
from strands.models.openai import OpenAIModel

def build_model() -> OpenAIModel:
    return OpenAIModel(
        client_args={
            "api_key": os.environ["OPENROUTER_API_KEY"],
            "base_url": "https://openrouter.ai/api/v1",
            "default_headers": {
                "HTTP-Referer": "https://localhost",
                "X-Title": "Strands QA Agent",
            },
        },
        model_id=os.environ.get("MODEL", "deepseek/deepseek-chat"),
        params={"temperature": 0.2},
    )
```

---

## 4. Create the MCP Server

`FastMCP` (from the official `mcp` package) gives you decorator-based tools and a
streamable HTTP transport that Strands' `MCPClient` can connect to.

### `src/qa_agent/mcp_server.py`
```python
from mcp.server.fastmcp import FastMCP

from .tools.web_tools import scrape_page, take_screenshot
from .tools.spell_tool import check_spelling
from .tools.template_tool import analyse_template, analyse_template_text
from .tools.compliance_tool import check_compliance

mcp = FastMCP("QA Tools", host="0.0.0.0", port=3001)

@mcp.tool()
def scrape(url: str) -> dict:
    """Fetch a page and return title, text, headings, links, and images."""
    return scrape_page(url)

@mcp.tool()
def screenshot(url: str, selector: str | None = None, full_page: bool = True) -> dict:
    """Capture a base64 PNG of a page (or a single selector)."""
    return {"img": take_screenshot(url, selector=selector, full_page=full_page)}

@mcp.tool()
def spell(text: str) -> dict:
    """Run a UK English spelling/grammar check and return structured issues."""
    return check_spelling(text)

@mcp.tool()
def template(image_path: str | None = None, text: str | None = None) -> dict:
    """Interpret a QA template (image via OCR, or raw text) into rules."""
    if text:
        return analyse_template_text(text)
    return analyse_template(image_path)

@mcp.tool()
def compliance(page_text: str, headings: list, rules: list) -> dict:
    """Audit a page against a list of template rules."""
    return check_compliance(page_text=page_text, headings=headings, rules=rules)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Run it:
```bash
python -m qa_agent.mcp_server
```

---

## 5. Custom Tools

### 5.1 Web Scraper

```python
# src/qa_agent/tools/web_tools.py
from playwright.sync_api import sync_playwright

def _with_page(url: str, fn):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            return fn(page)
        finally:
            browser.close()

def scrape_page(url: str) -> dict:
    def _scrape(page):
        return {
            "url": url,
            "title": page.title(),
            "text": page.inner_text("body"),
            "headings": page.eval_on_selector_all(
                "h1, h2, h3",
                "els => els.map(e => ({tag: e.tagName.toLowerCase(), text: e.innerText.trim()}))",
            ),
            "links": page.eval_on_selector_all(
                "a[href]",
                "els => els.slice(0, 200).map(e => ({text: e.innerText.trim(), href: e.getAttribute('href')}))",
            ),
            "images": page.eval_on_selector_all(
                "img",
                "els => els.slice(0, 200).map(e => ({alt: e.getAttribute('alt') || '', src: e.getAttribute('src')}))",
            ),
        }
    return _with_page(url, _scrape)
```

### 5.2 Screenshot Tool

```python
import base64

def take_screenshot(url: str, selector: str | None = None, full_page: bool = True) -> str:
    def _shot(page):
        if selector:
            el = page.query_selector(selector)
            if not el:
                raise RuntimeError(f"Selector not found: {selector}")
            return base64.b64encode(el.screenshot()).decode()
        return base64.b64encode(page.screenshot(full_page=full_page)).decode()
    return _with_page(url, _shot)
```

### 5.3 Spell & Grammar Checker (UK English)

The checker forces JSON output with a strict schema so the agent can act on it.

```python
# src/qa_agent/tools/spell_tool.py
from ..llm_client import call_llm_json

SYSTEM = (
    "You are a meticulous UK English copy editor. Use UK spelling "
    "(colour, organisation, analyse, behaviour, programme). Flag every "
    "spelling, grammar, punctuation, and tense issue. Ignore navigation "
    "labels, cookie notices, and footer boilerplate."
)

SCHEMA = """Return JSON:
{"issues":[{"type":"Spelling|Grammar|Punctuation|Style",
            "severity":"Critical|Minor|Info",
            "excerpt":"...","description":"...","suggestion":"..."}]}"""

def check_spelling(text: str) -> dict:
    prompt = f"{SCHEMA}\n\nTEXT TO REVIEW:\n\"\"\"{text[:12000]}\"\"\""
    result = call_llm_json(prompt, system=SYSTEM)
    return {"issues": result.get("issues", [])}
```

### 5.4 Template Interpreter (image-based QA)

```python
# src/qa_agent/tools/template_tool.py
import os
import base64
from pathlib import Path

import pytesseract
from PIL import Image
from io import BytesIO

from ..llm_client import call_llm_json

if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

SYSTEM = (
    "You convert QA template documents (often supplied as images) into a "
    "structured rule set that another QA agent can apply against a course web page."
)

SCHEMA = """Return JSON:
{"summary":"...",
 "rules":[{"id":"R1","category":"Content|Structure|Style|Accessibility|Branding|Other",
           "rule":"...","severity":"Critical|Minor|Info"}]}"""

def _load(image: str) -> Image.Image:
    if image.startswith("data:"):
        return Image.open(BytesIO(base64.b64decode(image.split(",", 1)[1])))
    p = Path(image)
    if p.exists():
        return Image.open(p)
    return Image.open(BytesIO(base64.b64decode(image)))

def analyse_template(image: str) -> dict:
    ocr_text = pytesseract.image_to_string(_load(image)).strip()
    if not ocr_text:
        return {"summary": "Empty template (no OCR text).", "rules": [], "ocr_text": ""}
    result = call_llm_json(f"{SCHEMA}\n\nOCR TEXT:\n\"\"\"{ocr_text}\"\"\"", system=SYSTEM)
    return {"summary": result.get("summary", ""), "rules": result.get("rules", []), "ocr_text": ocr_text}

def analyse_template_text(text: str) -> dict:
    if not text.strip():
        return {"summary": "Empty template.", "rules": []}
    result = call_llm_json(f"{SCHEMA}\n\nTEMPLATE TEXT:\n\"\"\"{text}\"\"\"", system=SYSTEM)
    return {"summary": result.get("summary", ""), "rules": result.get("rules", [])}
```

---

## 6. Strands Agent Setup

The agent connects to the MCP server over streamable HTTP, lists the tools it
exposes, and uses the OpenRouter-backed model to call them.

### `src/qa_agent/agent.py`
```python
from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

from .llm import build_model

SYSTEM_PROMPT = (
    "You are a course-page QA agent. For a given URL you must: "
    "1) call `scrape` to fetch the page; "
    "2) if a template was provided, call `template`; "
    "3) call `spell` on the page text; "
    "4) if you have rules, call `compliance`; "
    "5) call `screenshot` for evidence; "
    "6) return a single JSON object with keys course_name, url, "
    "template_summary, issues. UK English throughout."
)

def build_agent(mcp_url: str = "http://localhost:3001/mcp") -> tuple[Agent, MCPClient]:
    client = MCPClient(lambda: streamablehttp_client(mcp_url))
    client.start()
    tools = client.list_tools_sync()
    agent = Agent(model=build_model(), tools=tools, system_prompt=SYSTEM_PROMPT)
    return agent, client
```

---

## 7. QA Execution Flow

1. Input course URL (and optionally a template image)
2. Agent calls `scrape` → page content
3. Agent calls `template` (if template provided) → rule list
4. Agent calls `spell` → UK English issues
5. Agent calls `compliance` → page-vs-rules issues
6. Agent calls `screenshot` → base64 evidence
7. Agent returns a structured JSON report

The orchestration is driven by the model, but a deterministic Python "pipeline"
runner is also provided in `src/qa_agent/pipeline.py` for cases where you want
predictable execution (e.g. CI).

---

## 8. Generate QA Report

Example report shape:
```json
{
  "course_name": "Course Title",
  "url": "https://example.com",
  "generated_at": "2026-04-27T10:00:00Z",
  "template_summary": "Course page must include outcomes, duration, and CTA.",
  "issues": [
    {
      "type": "Spelling",
      "severity": "Minor",
      "excerpt": "color theory",
      "description": "US spelling 'color' used instead of UK 'colour'.",
      "suggestion": "colour theory",
      "screenshot": "<base64 png>"
    }
  ]
}
```

---

## 9. PDF Report Generation (ReportLab)

```python
# src/qa_agent/tools/report_tool.py
from pathlib import Path
from io import BytesIO
import base64

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak

SEVERITY_COLOURS = {"Critical": "#c0392b", "Minor": "#d68910", "Info": "#2874a6"}

def generate_pdf(report: dict, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(out_path, pagesize=A4, title=report.get("course_name", "QA Report"))
    flow = [
        Paragraph(report.get("course_name", "QA Report"), styles["Title"]),
        Paragraph(report.get("url", ""), styles["Normal"]),
        Paragraph(f"Generated: {report.get('generated_at', '')}", styles["Italic"]),
        Spacer(1, 0.5 * cm),
    ]
    if report.get("template_summary"):
        flow.append(Paragraph(f"<b>Template:</b> {report['template_summary']}", styles["Normal"]))
        flow.append(Spacer(1, 0.5 * cm))

    for i, issue in enumerate(report.get("issues", []), start=1):
        flow.append(PageBreak())
        colour = SEVERITY_COLOURS.get(issue.get("severity", "Info"), "#333333")
        flow.append(Paragraph(
            f'<font color="{colour}">Issue {i} — {issue.get("type", "Issue")} '
            f'({issue.get("severity", "Info")})</font>', styles["Heading2"]))
        if issue.get("ruleId"):
            flow.append(Paragraph(f"<b>Rule:</b> {issue['ruleId']}", styles["Normal"]))
        flow.append(Paragraph(f"<b>Description:</b> {issue.get('description', '')}", styles["Normal"]))
        if issue.get("excerpt"):
            flow.append(Paragraph(f"<b>Excerpt:</b> <i>“{issue['excerpt']}”</i>", styles["Normal"]))
        if issue.get("suggestion"):
            flow.append(Paragraph(f"<b>Suggestion:</b> {issue['suggestion']}", styles["Normal"]))
        if issue.get("screenshot"):
            try:
                img = Image(BytesIO(base64.b64decode(issue["screenshot"])), width=15 * cm, height=10 * cm)
                flow.append(Spacer(1, 0.4 * cm))
                flow.append(img)
            except Exception:
                flow.append(Paragraph("<i>(screenshot could not be embedded)</i>", styles["Italic"]))

    doc.build(flow)
    return out_path
```

---

## 10. Run Locally

### Terminal 1 — start the MCP server
```bash
python -m qa_agent.mcp_server
```

### Terminal 2 — run the agent CLI
```bash
# URL only (agent decides which tools to call)
python -m qa_agent.main --url https://example.com/course

# With an image-based QA template
python -m qa_agent.main --url https://example.com/course --template ./qa-template.png

# With a text template
python -m qa_agent.main --url https://example.com/course --template-text "Headings sentence case. Page must include learning outcomes."

# Deterministic pipeline mode (no LLM tool-routing — use for CI)
python -m qa_agent.main --url https://example.com/course --pipeline
```

Both a JSON and a PDF report are written under `reports/`.

---

## 11. Enhancements (Recommended)

- DOM element-level screenshot capture (already supported via the `selector` arg)
- Semantic diffing against the template (embeddings + cosine similarity)
- Severity levels (Critical / Minor / Info) — already wired in
- Persist reports to a database (Postgres / SQLite)
- A small UI dashboard (Streamlit or FastAPI + HTMX)

---

## 12. Key Notes

- UK English is enforced in every prompt — keep it that way.
- Strands' `OpenAIModel` works against any OpenAI-compatible endpoint, so swapping
  DeepSeek for another OpenRouter model is just a `MODEL=` change in `.env`.
- Cache LLM responses (e.g. `diskcache`) to keep cost down on repeat runs.
- Handle dynamic pages by tweaking `wait_until` / `timeout` in `web_tools.py`.
- The MCP server uses streamable-HTTP transport so any MCP-aware client (Claude
  Desktop, IDEs, Strands) can use the same tools.

---

## Conclusion

You now have a complete Python setup using the real **Strands Agents SDK** to build
an AI QA agent capable of:
- Website analysis
- Template-based validation
- Intelligent issue detection
- Automated PDF reporting

This system is scalable and can later be deployed beyond the local environment.
