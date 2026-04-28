from __future__ import annotations

import os

import pytesseract
from PIL import Image

from ..llm_client import call_llm_json
from ..security import safe_resolve_template, truncate_text

if os.environ.get("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

SYSTEM = (
    "You convert QA template documents (often supplied as images) into a "
    "structured rule set that another QA agent can apply against a course web page."
)

SCHEMA_INSTRUCTION = """Return a JSON object with this shape:
{
  "summary": "<one-sentence summary of the template>",
  "rules": [
    {
      "id": "R1",
      "category": "Content" | "Structure" | "Style" | "Accessibility" | "Branding" | "Other",
      "rule": "<the rule, phrased as a check>",
      "severity": "Critical" | "Minor" | "Info"
    }
  ]
}
Output ONLY the JSON object."""


def analyse_template(image_path: str) -> dict:
    """OCR a template image and convert it into a rule list.

    Only filesystem paths under the configured allowed roots are accepted; raw
    base64 / data URIs are rejected on purpose to avoid being a generic OCR
    endpoint that processes attacker-supplied bytes.
    """
    safe_path = safe_resolve_template(image_path)
    with Image.open(safe_path) as img:
        ocr_text = pytesseract.image_to_string(img).strip()

    if not ocr_text:
        return {"summary": "Empty template (no text extracted by OCR).", "rules": [], "ocr_text": ""}
    prompt = f'{SCHEMA_INSTRUCTION}\n\nOCR TEXT FROM TEMPLATE:\n"""{truncate_text(ocr_text)}"""'
    result = call_llm_json(prompt, system=SYSTEM)
    return {
        "summary": result.get("summary", ""),
        "rules": result.get("rules") or [],
        "ocr_text": ocr_text,
    }


def analyse_template_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"summary": "Empty template.", "rules": []}
    prompt = f'{SCHEMA_INSTRUCTION}\n\nTEMPLATE TEXT:\n"""{truncate_text(text)}"""'
    result = call_llm_json(prompt, system=SYSTEM)
    return {"summary": result.get("summary", ""), "rules": result.get("rules") or []}
