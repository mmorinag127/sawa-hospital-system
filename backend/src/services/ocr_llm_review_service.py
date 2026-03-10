from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any


TextTruncator = Callable[[str, int], str]
PromptTableCompactor = Callable[[dict[str, Any]], list[dict[str, Any]]]
PromptCellIssueCompactor = Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]


def build_llm_review_prompt_rows(
    *,
    fields: list[str],
    rows: list[list[str]],
    row_ids: list[str],
) -> list[dict[str, Any]]:
    prompt_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        row_id = row_ids[idx] if idx < len(row_ids) and row_ids[idx] else f"row-{idx + 1}"
        entry: dict[str, Any] = {"row_id": row_id}
        for col_idx, field in enumerate(fields):
            entry[field] = row[col_idx] if col_idx < len(row) else ""
        prompt_rows.append(entry)
    return prompt_rows


def build_llm_review_payload_rows(
    *,
    fields: list[str],
    rows: list[list[str]],
) -> list[dict[str, str]]:
    payload_rows: list[dict[str, str]] = []
    for row in rows:
        entry = {
            field: row[idx] if idx < len(row) else ""
            for idx, field in enumerate(fields)
        }
        if any(str(value or "").strip() for value in entry.values()):
            payload_rows.append(entry)
    return payload_rows


def resolve_llm_review_row_ids(
    *,
    baseline_row_ids: list[str],
    row_count: int,
) -> list[str]:
    resolved: list[str] = []
    for idx in range(max(row_count, 0)):
        candidate = str(baseline_row_ids[idx] if idx < len(baseline_row_ids) else "").strip()
        resolved.append(candidate or f"row-{idx + 1}")
    return resolved


def build_llm_review_response_schema(fields: list[str]) -> dict[str, Any]:
    row_properties: dict[str, Any] = {}
    for field in fields:
        row_properties[field] = {"type": "string"}
    issue_schema = {
        "type": "object",
        "properties": {
            "issue_id": {"type": "string"},
            "row_id": {"type": "string"},
            "row_index": {"type": "integer"},
            "field": {"type": "string"},
            "issue_code": {"type": "string"},
            "status": {"type": "string"},
            "page_index": {"type": "integer"},
            "table_id": {"type": "string"},
            "current_text": {"type": "string"},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
            "reason": {"type": "string"},
            "severity": {"type": "string"},
        },
        "required": ["field"],
    }
    table_schema = {
        "type": "object",
        "properties": {
            "table_id": {"type": "string"},
            "page_index": {"type": "integer"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "required": ["rows"],
    }
    return {
        "type": "object",
        "properties": {
            "facility_name": {"type": "string"},
            "date_strings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": row_properties,
                    "required": list(fields),
                },
            },
            "table_raw": {"type": "string"},
            "tables": {
                "type": "array",
                "items": table_schema,
            },
            "cell_issues": {
                "type": "array",
                "items": issue_schema,
            },
            "llm_review": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "needs_more_review": {"type": "boolean"},
                    "notes": {"type": "string"},
                    "issues": {
                        "type": "array",
                        "items": issue_schema,
                    },
                },
                "required": ["status", "needs_more_review", "notes"],
            },
        },
        "required": ["facility_name", "date_strings", "rows", "llm_review"],
    }


def build_llm_review_prompts(
    *,
    provider: str,
    template: dict[str, Any],
    baseline: dict[str, Any],
    pdf_variant_requested: str = "raw",
    pdf_variant_used: str = "raw",
    pdf_variant_fallback_reason: str | None = None,
    prompt_override: str | None = None,
    truncate_assist_text: TextTruncator,
    compact_prompt_tables: PromptTableCompactor,
    compact_prompt_cell_issues: PromptCellIssueCompactor,
) -> tuple[str, str]:
    prompt_key = "openai_ocr_prompt" if provider == "openai" else "gemini_ocr_prompt"
    user_key = "openai_ocr_user_prompt" if provider == "openai" else "gemini_ocr_user_prompt"
    base_system_prompt = str(template.get(prompt_key) or "").strip()
    base_user_prompt = str(prompt_override or template.get(user_key) or "").strip()
    baseline_fields = [str(field).strip() for field in (baseline.get("fields") or []) if str(field).strip()]
    baseline_rows = build_llm_review_prompt_rows(
        fields=baseline_fields,
        rows=baseline.get("rows") or [],
        row_ids=baseline.get("row_ids") or [],
    )
    raw_output = baseline.get("raw_output") if isinstance(baseline.get("raw_output"), dict) else {}

    system_sections: list[str] = []
    if base_system_prompt:
        system_sections.append(base_system_prompt)
    system_sections.append(
        "You are validating yomitoku OCR against the attached fax PDF/image.\n"
        "You will receive the fax PDF/image, the current baseline rows shown to the user, and the previous yomitoku/LLM OCR payload.\n"
        "Review the baseline against the fax and return a revised OCR payload in yomitoku-compatible JSON.\n"
        "Return strict JSON only with shape:\n"
        '{"facility_name":"","date_strings":[],"rows":[{}],"table_raw":"","tables":[{"table_id":"","page_index":1,"rows":[[]]}],"cell_issues":[{"issue_id":"","row_id":"","row_index":0,"field":"","issue_code":"","status":"","page_index":1,"table_id":"","current_text":"","confidence":0.0,"evidence":"","reason":"","severity":"warning"}],"llm_review":{"status":"","needs_more_review":false,"notes":"","issues":[{"issue_id":"","row_id":"","row_index":0,"field":"","issue_code":"","status":"","page_index":1,"table_id":"","current_text":"","confidence":0.0,"evidence":"","reason":"","severity":"warning"}]}}\n'
        "Rules:\n"
        "- Keep the row order aligned with the baseline rows.\n"
        "- Use only field names from the baseline schema in rows and issues.\n"
        "- rows, table_raw, and tables must describe the same reviewed table content.\n"
        "- Correct cells only when the fax image clearly supports the change.\n"
        "- If a cell remains unreadable or ambiguous, keep the safest value in rows and add an issue in cell_issues and llm_review.issues.\n"
        "- Use row_id values from the baseline rows when you report issues. If you cannot infer row_id, include row_index.\n"
        "- Confidence must be 0.00-1.00.\n"
        "- Return JSON only."
    )

    user_sections: list[str] = []
    if base_user_prompt:
        user_sections.append(base_user_prompt)
    user_sections.append(
        f"Attached fax variant requested: {pdf_variant_requested}\n"
        f"Attached fax variant used: {pdf_variant_used}"
    )
    if pdf_variant_fallback_reason:
        user_sections.append(f"Attached fax fallback reason: {pdf_variant_fallback_reason}")
    baseline_revision_id = str(baseline.get("baseline_revision_id") or "").strip()
    if baseline_revision_id:
        user_sections.append(f"Current baseline revision_id: {baseline_revision_id}")
    user_sections.append(
        f"Current baseline source: {str(baseline.get('baseline_source') or 'yomitoku').strip()}"
    )
    if baseline_fields:
        user_sections.append(
            "Valid baseline fields:\n"
            f"{json.dumps(baseline_fields, ensure_ascii=False)}"
        )
    user_sections.append(
        "Current baseline rows:\n"
        f"{truncate_assist_text(json.dumps(baseline_rows[:200], ensure_ascii=False), 12000)}"
    )
    user_sections.append(
        "Return rows using this exact baseline field schema:\n"
        f"{json.dumps(baseline_fields, ensure_ascii=False)}"
    )
    table_raw = raw_output.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        user_sections.append(
            "Previous yomitoku/LLM markdown:\n"
            f"{truncate_assist_text(table_raw.strip(), 7000)}"
        )
    tables = compact_prompt_tables(raw_output)
    if tables:
        user_sections.append(
            "Previous yomitoku/LLM structured tables/cells:\n"
            f"{truncate_assist_text(json.dumps(tables, ensure_ascii=False), 10000)}"
        )
    issues = compact_prompt_cell_issues(raw_output, template)
    if issues:
        user_sections.append(
            "Existing suspicious cells from yomitoku/LLM:\n"
            f"{truncate_assist_text(json.dumps(issues, ensure_ascii=False), 6000)}"
        )
    user_sections.append(
        "Review the baseline against the fax image and return a revised yomitoku-compatible payload only."
    )
    return "\n\n".join(system_sections), "\n\n".join(user_sections)
