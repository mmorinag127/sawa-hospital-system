#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.services.gemini_ocr_service import run_gemini_ocr
from src.services.openai_ocr_service import run_openai_ocr


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return loaded


def _load_template(
    *,
    template_json: str | None,
    facility_id: str | None,
) -> dict[str, Any]:
    template: dict[str, Any] = {}

    if facility_id:
        from src.services.config_service import get_facility_config

        facility_config = get_facility_config(facility_id)
        if not facility_config:
            raise ValueError(f"facility not found: {facility_id}")
        fax_template = facility_config.get("fax_template")
        if isinstance(fax_template, dict):
            template.update(deepcopy(fax_template))

    if template_json:
        payload = _read_json(Path(template_json))
        source = payload.get("fax_template") if isinstance(payload.get("fax_template"), dict) else payload
        template.update(deepcopy(source))

    return template


def _apply_provider_overrides(
    *,
    template: dict[str, Any],
    provider: str,
    model: str | None,
    prompt: str | None,
    max_tokens: int | None,
    timeout_seconds: float | None,
    resolution: int | None,
    page: int | None,
) -> dict[str, Any]:
    patched = deepcopy(template)

    if page and page > 0:
        patched["page"] = page
    if resolution and resolution > 0:
        patched[f"{provider}_ocr_resolution"] = resolution
        patched["main_ocr_resolution"] = resolution
    if max_tokens and max_tokens > 0:
        patched[f"{provider}_ocr_max_tokens"] = max_tokens
    if timeout_seconds and timeout_seconds > 0:
        patched[f"{provider}_ocr_timeout_seconds"] = timeout_seconds
    if model and model.strip():
        patched[f"{provider}_ocr_model"] = model.strip()
    if prompt and prompt.strip():
        patched[f"{provider}_ocr_prompt"] = prompt.strip()

    patched.setdefault("main_ocr_row_fields", ["date_mmdd", "daypart", "menu", "remarks"])
    patched[f"{provider}_ocr_enabled"] = True
    patched[f"{provider}_ocr_fallback_provider"] = "none"
    return patched


def _rows_to_table_rows(rows: Any, row_fields: list[str]) -> list[list[str]]:
    if not isinstance(rows, list):
        return []
    table_rows: list[list[str]] = []
    for row in rows:
        if isinstance(row, dict):
            table_rows.append([str(row.get(field, "") or "") for field in row_fields])
        else:
            table_rows.append([str(row)])
    return table_rows


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _to_markdown_report(output: dict[str, Any]) -> str:
    lines: list[str] = []
    provider = str(output.get("provider") or "")
    model = str(output.get("model") or "")
    facility_name = str(output.get("facility_name") or "")
    date_strings = output.get("date_strings") if isinstance(output.get("date_strings"), list) else []
    row_fields = output.get("row_fields") if isinstance(output.get("row_fields"), list) else []
    table_rows = output.get("table_rows") if isinstance(output.get("table_rows"), list) else []

    lines.append("# VLM OCR Result")
    lines.append("")
    lines.append(f"- provider: `{provider}`")
    lines.append(f"- model: `{model}`")
    lines.append(f"- facility_name: `{facility_name}`")
    lines.append(f"- rows: `{len(table_rows)}`")
    if date_strings:
        joined_dates = ", ".join(str(item) for item in date_strings if str(item).strip())
        lines.append(f"- date_strings: {joined_dates}")
    lines.append("")

    if not row_fields:
        lines.append("## Table")
        lines.append("")
        lines.append("_No row fields_")
        return "\n".join(lines)

    headers = [str(field) for field in row_fields]
    lines.append("## Table")
    lines.append("")
    lines.append("| " + " | ".join(_escape_markdown_cell(col) for col in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in table_rows:
        if isinstance(row, list):
            cells = [str(cell) if cell is not None else "" for cell in row]
        else:
            cells = [str(row)]
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]
        lines.append("| " + " | ".join(_escape_markdown_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local VLM OCR test against a PDF using OpenAI or Gemini."
    )
    parser.add_argument("--pdf", required=True, help="Path to input PDF file.")
    parser.add_argument(
        "--provider",
        required=True,
        choices=["openai", "gemini"],
        help="VLM provider to test.",
    )
    parser.add_argument(
        "--template-json",
        default=None,
        help="Optional JSON file. Accepts template object or {'fax_template': {...}}.",
    )
    parser.add_argument(
        "--facility-id",
        default=None,
        help="Optional facility_id to load template from DB.",
    )
    parser.add_argument("--model", default=None, help="Override model name.")
    parser.add_argument("--prompt", default=None, help="Inline prompt override.")
    parser.add_argument("--prompt-file", default=None, help="Path to prompt text file.")
    parser.add_argument("--max-tokens", type=int, default=None, help="Max output tokens override.")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout seconds override.")
    parser.add_argument("--resolution", type=int, default=None, help="PDF render DPI override.")
    parser.add_argument("--page", type=int, default=None, help="1-based page index override.")
    parser.add_argument("--output", default=None, help="Optional JSON output path.")
    parser.add_argument("--markdown-output", default=None, help="Optional Markdown report output path.")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()

    prompt_override = args.prompt
    if args.prompt_file:
        prompt_override = Path(args.prompt_file).read_text(encoding="utf-8")

    template = _load_template(template_json=args.template_json, facility_id=args.facility_id)
    template = _apply_provider_overrides(
        template=template,
        provider=args.provider,
        model=args.model,
        prompt=prompt_override,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        resolution=args.resolution,
        page=args.page,
    )

    if args.provider == "openai":
        payload = run_openai_ocr(pdf_bytes=pdf_bytes, template=template, facility_id=args.facility_id)
    else:
        payload = run_gemini_ocr(pdf_bytes=pdf_bytes, template=template, facility_id=args.facility_id)

    row_fields = [str(field) for field in (template.get("main_ocr_row_fields") or [])]
    output = {
        "provider": args.provider,
        "model": template.get(f"{args.provider}_ocr_model"),
        "facility_name": payload.get("facility_name"),
        "date_strings": payload.get("date_strings"),
        "row_fields": row_fields,
        "table_rows": _rows_to_table_rows(payload.get("rows"), row_fields),
        "raw": payload,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    if args.markdown_output:
        markdown = _to_markdown_report(output)
        Path(args.markdown_output).write_text(markdown, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
