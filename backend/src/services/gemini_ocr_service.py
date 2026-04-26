from __future__ import annotations

import base64
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.services.pdf_render import render_pdf_to_png_bytes
from src.services.template_field_schema_service import (
    build_header_by_field,
    classify_aux_header_semantic,
    derive_row_fields_from_columns,
)

_DEFAULT_ROW_FIELDS = ["date_mmdd", "daypart", "menu", "remarks"]
_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "／": "/",
        "－": "-",
        "ー": "-",
        "　": " ",
    }
)


def _row_fields(template: dict) -> list[str]:
    derived = derive_row_fields_from_columns(template.get("columns"))
    if bool(template.get("columns_authoritative")) and derived:
        return derived
    fields = template.get("main_ocr_row_fields")
    if isinstance(fields, list):
        normalized: list[str] = []
        for field in fields:
            text = str(field or "").strip()
            if text:
                normalized.append(text)
        if normalized:
            return normalized
    if derived:
        return derived
    return list(_DEFAULT_ROW_FIELDS)


def _is_qty_field(field: str) -> bool:
    return field.startswith("qty.")


def _is_date_field(field: str) -> bool:
    key = field.lower()
    return key in {"date_mmdd", "date"} or key.startswith("date")


def _is_daypart_field(field: str) -> bool:
    return field in {"daypart", "meal", "time"}


def _is_aux_field(field: str) -> bool:
    return field.startswith("aux.")


def _is_remarks_field(field: str) -> bool:
    return field in {"remarks", "note"}


def _full_table_patch_fields(row_fields: list[str]) -> list[str]:
    return [
        field
        for field in row_fields
        if _is_qty_field(field) or _is_aux_field(field) or _is_remarks_field(field)
    ]


def _row_has_field(row: dict[str, Any], field: str) -> bool:
    if field in row:
        return True
    current: Any = row
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current.get(part)
    return True


def _normalize_digits_text(value: str) -> str:
    normalized = (
        value.translate(_FULLWIDTH_TRANSLATION)
        .replace(",", "")
        .replace("，", "")
        .replace(" ", "")
    )
    if re.fullmatch(r"\d+", normalized):
        return normalized
    return ""


def _normalize_daypart_text(value: str) -> str:
    normalized = value.translate(_FULLWIDTH_TRANSLATION)
    if "朝" in normalized:
        return "朝"
    if "昼" in normalized:
        return "昼"
    if "夕" in normalized or "夜" in normalized:
        return "夕"
    return ""


def _normalize_date_text(value: str) -> str:
    normalized = value.translate(_FULLWIDTH_TRANSLATION).replace(" ", "")
    match = re.search(r"\d{4}[/-](\d{1,2})[/-](\d{1,2})", normalized)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month}/{day}"
        return ""
    match = re.search(r"(\d{1,2})[/-](\d{1,2})", normalized)
    if not match:
        return ""
    month = int(match.group(1))
    day = int(match.group(2))
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month}/{day}"
    return ""


def _normalize_row_cell(field: str, value: object, *, full_table_mode: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_qty_field(field):
        return _normalize_digits_text(text)
    if full_table_mode:
        return text
    if _is_daypart_field(field):
        return _normalize_daypart_text(text)
    if _is_date_field(field):
        return _normalize_date_text(text)
    return text


def _normalize_row_index(value: object) -> str:
    if value is None:
        return ""
    text = str(value).translate(_FULLWIDTH_TRANSLATION).strip()
    if not re.fullmatch(r"\d+", text):
        return ""
    return str(int(text))


def _quantity_only_mode(template: dict[str, Any]) -> bool:
    return _as_bool(template.get("llm_quantity_only_mode"), default=False)


def _full_table_mode(template: dict[str, Any]) -> bool:
    return _as_bool(template.get("llm_full_table_mode"), default=False)


def _resolve_timeout_seconds(template: dict[str, Any]) -> float:
    configured = template.get("gemini_ocr_timeout_seconds")
    if configured is not None:
        return float(configured)
    if _full_table_mode(template):
        return float(
            os.getenv(
                "GEMINI_OCR_FULL_TABLE_TIMEOUT_SECONDS",
                os.getenv("GEMINI_OCR_TIMEOUT_SECONDS", "240"),
            )
        )
    return float(os.getenv("GEMINI_OCR_TIMEOUT_SECONDS", "90"))


def _quantity_fields(row_fields: list[str]) -> list[str]:
    return [field for field in row_fields if _is_qty_field(field)]


def _normalize_rows(
    rows: object,
    row_fields: list[str],
    *,
    quantity_only_mode: bool,
    full_table_mode: bool,
) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    qty_fields = _quantity_fields(row_fields)
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_row: dict[str, str] = {}
        non_empty = False
        if quantity_only_mode and qty_fields:
            target_fields = qty_fields
        elif full_table_mode:
            target_fields = _full_table_patch_fields(row_fields)
        else:
            target_fields = row_fields
        for field in target_fields:
            if full_table_mode and not _row_has_field(row, field):
                continue
            cell = _normalize_row_cell(field, row.get(field), full_table_mode=full_table_mode)
            normalized_row[field] = cell
            if cell:
                non_empty = True
        if quantity_only_mode or full_table_mode:
            row_index = _normalize_row_index(row.get("row_index"))
            if row_index:
                normalized_row["row_index"] = row_index
                non_empty = True
            elif full_table_mode:
                continue
        if non_empty or (full_table_mode and not quantity_only_mode):
            normalized.append(normalized_row)
    return normalized


def _normalize_payload(
    payload: dict[str, Any],
    *,
    row_fields: list[str],
    quantity_only_mode: bool,
    full_table_mode: bool,
) -> dict[str, Any]:
    normalized = dict(payload)

    raw_name = normalized.get("facility_name")
    facility_name = str(raw_name or "").strip() or None

    raw_dates = normalized.get("date_strings")
    date_strings: list[str] = []
    if isinstance(raw_dates, list):
        for item in raw_dates:
            date_text = _normalize_date_text(str(item))
            if date_text and date_text not in date_strings:
                date_strings.append(date_text)

    normalized["facility_name"] = facility_name
    normalized["date_strings"] = date_strings
    normalized["rows"] = _normalize_rows(
        normalized.get("rows"),
        row_fields,
        quantity_only_mode=quantity_only_mode,
        full_table_mode=full_table_mode,
    )
    if full_table_mode:
        returned_row_indexes: list[int] = []
        for row in normalized["rows"]:
            if not isinstance(row, dict):
                continue
            row_index = _normalize_row_index(row.get("row_index"))
            if not row_index:
                continue
            try:
                returned_row_indexes.append(int(row_index))
            except Exception:
                continue
        if returned_row_indexes:
            normalized["_ocr_returned_row_indexes"] = sorted(set(returned_row_indexes))
    return normalized


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return int(fallback)


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    return key


def _get_model(template: dict) -> str:
    model = (
        str(template.get("gemini_ocr_model") or "").strip()
        or os.getenv("GEMINI_OCR_MODEL", "").strip()
        or "gemini-2.5-flash"
    )
    return model


def _model_requires_thinking_mode(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return "gemini-2.5-pro" in normalized


def _resolve_thinking_budget(template: dict[str, Any], model: str) -> int | None:
    configured = template.get("gemini_ocr_thinking_budget")
    if configured is None:
        env_value = os.getenv("GEMINI_OCR_THINKING_BUDGET", "").strip()
        configured = env_value or None
    if configured is not None:
        return _safe_int(configured, 0)
    if _model_requires_thinking_mode(model):
        return 512
    return 0


def _build_prompt(template: dict) -> str:
    fields = _row_fields(template)
    quantity_only_mode = _quantity_only_mode(template)
    full_table_mode = _full_table_mode(template)
    qty_fields = _quantity_fields(fields)
    patch_fields = _full_table_patch_fields(fields)
    field_list = ", ".join(fields)
    qty_field_list = ", ".join(qty_fields)
    patch_field_list = ", ".join(patch_fields)
    header_by_field = build_header_by_field(template.get("columns"))
    custom = str(template.get("gemini_ocr_prompt") or "").strip()
    if quantity_only_mode and qty_fields:
        base_prompt = (
            "You are an OCR parser for Japanese fax order forms.\n"
            "Extract only quantity cells from the table body and return strict JSON.\n"
            "Return ONLY one JSON object with this exact shape:\n"
            '{"facility_name": string, "date_strings": string[], "rows": object[]}.\n'
            "facility_name must be empty string when unreadable.\n"
            "Each row object in rows must include:\n"
            "- row_index: zero-based table body row index\n"
            f"- quantity fields only: {qty_field_list}\n"
            "Rules:\n"
            "- Read table body rows from top to bottom and preserve row order.\n"
            "- Emit one row object per visible table body row (do not skip rows).\n"
            "- For unreadable quantity cells, set empty string.\n"
            "- Quantity fields (qty.*) must be digits only ([0-9]+), otherwise empty string.\n"
            "- If a handwritten quantity is unreadable, infer only from nearby recognized quantities when continuity is clear; otherwise keep empty string.\n"
            "- If a parenthesis/bracket mark spans multiple quantity cells with one number, copy that number to every covered cell.\n"
            "- If arrows/vertical range lines indicate a number applies to a span, copy that number to all cells in that span.\n"
            "- Apply copying/inference only within the clearly indicated range.\n"
            "- Do not output date/daypart/menu fields in rows.\n"
            "- Skip headers, legends, totals, page numbers, and notes outside table body.\n"
            "- Do not add extra keys.\n"
            "- Do not output markdown, code fences, or explanations."
        )
    elif full_table_mode:
        field_rules: list[str] = []
        for field in patch_fields:
            header = str(header_by_field.get(field) or field).strip()
            if _is_qty_field(field):
                field_rules.append(
                    f"- {field} ({header}) is a quantity field. Copy only the digits visible in that exact quantity column. Never move helper/total numbers into this field."
                )
            elif _is_aux_field(field):
                semantic = classify_aux_header_semantic(header)
                if semantic == "block_total":
                    field_rules.append(
                        f"- {field} ({header}) is a display-only helper/total column. Copy the visible helper/total text only into this field. Never move it into qty.* or remarks."
                    )
                elif semantic == "slot_label":
                    field_rules.append(
                        f"- {field} ({header}) is a display-only slot/category label column. Return only normalized labels such as 主, 主Ａ, 主Ｂ, 副①, or 副② when clearly supported by the fax cell. Leave it empty when the mark is noisy or unreadable."
                    )
                else:
                    field_rules.append(
                        f"- {field} ({header}) is display-only auxiliary text. Copy the visible text exactly. Never reinterpret it as quantity or remarks."
                    )
            elif _is_remarks_field(field):
                field_rules.append(
                    f"- {field} ({header}) comes only from the actual notes/remarks cell. Do not move side legends, diet markers, or helper annotations into this field."
                )
        base_prompt = (
            "You are an OCR parser for Japanese fax order forms.\n"
            "Extract sparse current-sheet-aligned cell patches and return strict JSON.\n"
            "Return ONLY one JSON object with this exact shape:\n"
            '{"facility_name": string, "date_strings": string[], "rows": object[]}.\n'
            "facility_name must be empty string when unreadable.\n"
            "Each row object in rows must include row_index and may include only these patch fields:\n"
            f"row_index, {patch_field_list}\n"
            "Rules:\n"
            "- row_index is the zero-based structural row index from the current sheet/baseline.\n"
            "- Every returned row object must include row_index.\n"
            "- Return only rows that need a patch; omit unchanged rows.\n"
            "- Inside a returned row, omit unchanged fields.\n"
            "- To explicitly clear a cell, include that field with empty string.\n"
            "- Do not output structural anchor fields such as date_mmdd, daypart, or menu; those are owned by the current sheet.\n"
            "- Do not emit unanchored rows.\n"
            "- Skip header rows, legends, date-only separators, totals-only lines, page numbers, and notes outside the table body.\n"
            "- Copy only text visible in the same aligned row/cell.\n"
            "- For display-only helper cells, keep them empty when unreadable instead of guessing.\n"
            "- For quantity fields only, if handwriting is unreadable you may infer from nearby recognized quantities when continuity is clear; otherwise keep empty string.\n"
            "- Quantity fields (qty.*) must be digits only ([0-9]+), otherwise empty string.\n"
            f"{chr(10).join(field_rules)}\n"
            "- Do not add extra keys.\n"
            "- Do not output markdown, code fences, or explanations."
        )
    else:
        base_prompt = (
            "You are an OCR parser for Japanese fax order forms.\n"
            "Extract only the order table and return strict JSON.\n"
            "Return ONLY one JSON object with this exact shape:\n"
            '{"facility_name": string, "date_strings": string[], "rows": object[]}.\n'
            "facility_name must be empty string when unreadable.\n"
            "Each row object in rows must include all of these keys:\n"
            f"{field_list}\n"
            "Rules:\n"
            "- Read table body rows from top to bottom.\n"
            "- Copy only text visible in the same row/cell.\n"
            "- For date/daypart/menu fields, never infer missing values.\n"
            "- For quantity fields only, if handwriting is unreadable you may infer from nearby recognized quantities when continuity is clear; otherwise keep empty string.\n"
            "- For quantity fields, when parenthesis/arrow/vertical range marks indicate one number applies to multiple cells, copy that number across the indicated range.\n"
            "- For merged cells, keep missing fields as empty string.\n"
            "- Skip headers, legends, totals, page numbers, and notes outside table body.\n"
            "- Quantity fields (qty.*) must be digits only ([0-9]+), otherwise empty string.\n"
            "- Date fields must be M/D format when readable, otherwise empty string.\n"
            "- Daypart fields must be one of 朝, 昼, 夕; otherwise empty string.\n"
            "- Do not add extra keys.\n"
            "- Do not output markdown, code fences, or explanations."
        )
    if custom:
        return f"{base_prompt}\nFacility-specific instruction:\n{custom}"
    return base_prompt


def _build_user_prompt(template: dict) -> str:
    custom = str(template.get("gemini_ocr_user_prompt") or "").strip()
    if custom:
        return custom
    return "Read the attached fax image and return JSON following the system instruction."


def _build_response_schema(template: dict) -> dict[str, Any]:
    custom_schema = template.get("gemini_ocr_response_schema")
    if isinstance(custom_schema, dict) and custom_schema:
        return custom_schema
    fields = _row_fields(template)
    quantity_only_mode = _quantity_only_mode(template)
    qty_fields = _quantity_fields(fields)
    full_table_mode = _full_table_mode(template)
    if quantity_only_mode and qty_fields:
        target_fields = qty_fields
    elif full_table_mode:
        target_fields = _full_table_patch_fields(fields)
    else:
        target_fields = fields
    header_by_field = build_header_by_field(template.get("columns"))
    row_properties: dict[str, Any] = {}
    for field in target_fields:
        key = field
        if key.startswith("qty."):
            description = "Meal quantity as digits only. Empty string when unreadable."
        elif _is_aux_field(key):
            header = str(header_by_field.get(key) or key).strip()
            semantic = classify_aux_header_semantic(header)
            if semantic == "block_total":
                description = "Display-only helper or total cell text. Never use this field for meal quantity."
            elif semantic == "slot_label":
                description = "Display-only slot or category label. Return only normalized labels such as 主, 主Ａ, 主Ｂ, 副①, or 副②."
            else:
                description = "Display-only auxiliary cell text. Never use this field for meal quantity or remarks."
        elif key in {"date_mmdd", "date"} or key.startswith("date"):
            description = "Date in M/D format when visible."
        elif key in {"daypart", "meal", "time"}:
            description = "Daypart as one of 朝, 昼, 夕."
        elif key in {"menu", "menu_name"}:
            description = "Menu name text exactly as shown."
        elif key in {"remarks", "note"}:
            description = "Notes text from the actual notes/remarks cell only."
        else:
            description = "Cell text as string."
        row_properties[key] = {
            "type": "string",
            "description": description,
        }
    if quantity_only_mode and qty_fields:
        row_properties["row_index"] = {
            "type": "integer",
            "description": "Zero-based table body row index.",
        }
        required_fields = ["row_index", *target_fields]
    elif full_table_mode:
        row_properties["row_index"] = {
            "type": "integer",
            "description": "Zero-based structural row index from the current sheet/baseline.",
        }
        required_fields = ["row_index"]
    else:
        required_fields = [field for field in target_fields]
    return {
        "type": "object",
        "properties": {
            "facility_name": {
                "type": "string",
                "description": "Facility name if readable, otherwise empty string.",
            },
            "date_strings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Detected date strings from the form.",
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": row_properties,
                    "required": required_fields,
                },
            },
        },
        "required": ["facility_name", "date_strings", "rows"],
    }


def _extract_response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate, dict) else None
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            value = part.get("text")
            if isinstance(value, str):
                texts.append(value)
    return "\n".join(texts).strip()


def _extract_json_payload_with_meta(text: str) -> tuple[dict[str, Any], bool]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()
    if not raw.startswith("{"):
        first = raw.find("{")
        last = raw.rfind("}")
        if first >= 0 and last > first:
            raw = raw[first : last + 1]
    recovered_truncated_json = False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = _recover_truncated_payload(raw)
        if payload is None:
            raise
        recovered_truncated_json = True
    if not isinstance(payload, dict):
        raise ValueError("Gemini OCR response is not JSON object")
    return payload, recovered_truncated_json


def _extract_json_payload(text: str) -> dict[str, Any]:
    payload, _ = _extract_json_payload_with_meta(text)
    return payload


def _recover_truncated_payload(raw: str) -> dict[str, Any] | None:
    marker = '"rows"'
    marker_index = raw.find(marker)
    if marker_index < 0:
        return None

    facility_name: str | None = None
    facility_match = re.search(r'"facility_name"\s*:\s*(null|"([^"\\]|\\.)*")', raw, re.S)
    if facility_match:
        token = facility_match.group(1)
        if token != "null":
            try:
                parsed_name = json.loads(token)
                if isinstance(parsed_name, str):
                    facility_name = parsed_name
            except Exception:  # noqa: BLE001
                facility_name = None

    date_strings: list[str] = []
    dates_match = re.search(r'"date_strings"\s*:\s*(\[[^\]]*\])', raw, re.S)
    if dates_match:
        try:
            parsed_dates = json.loads(dates_match.group(1))
            if isinstance(parsed_dates, list):
                date_strings = [str(item) for item in parsed_dates if str(item).strip()]
        except Exception:  # noqa: BLE001
            date_strings = []

    rows_start = raw.find("[", marker_index)
    if rows_start < 0:
        return None
    rows_fragment = raw[rows_start + 1 :]
    row_chunks = re.findall(r"\{[^{}]*\}", rows_fragment, re.S)
    if not row_chunks:
        return None
    rows: list[dict[str, str]] = []
    for chunk in row_chunks:
        try:
            row_obj = json.loads(chunk)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(row_obj, dict):
            continue
        normalized_row: dict[str, str] = {}
        for key, value in row_obj.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            if value is None:
                normalized_row[key_text] = ""
            else:
                normalized_row[key_text] = str(value)
        if normalized_row:
            rows.append(normalized_row)
    if not rows:
        return None
    return {
        "facility_name": facility_name,
        "date_strings": date_strings,
        "rows": rows,
    }


def _api_url(model: str, api_key: str) -> str:
    base = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    encoded_model = urllib.parse.quote(model, safe="")
    encoded_key = urllib.parse.quote(api_key, safe="")
    return f"{base}/models/{encoded_model}:generateContent?key={encoded_key}"


def _extract_finish_reason(payload: dict[str, Any]) -> str | None:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict):
        return None
    finish_reason = first.get("finishReason")
    if not isinstance(finish_reason, str):
        return None
    normalized = finish_reason.strip()
    return normalized or None


def _is_truncated_finish_reason(finish_reason: str | None) -> bool:
    if not finish_reason:
        return False
    token = finish_reason.strip().upper().replace("-", "_").replace(" ", "_")
    if token in {"MAX_TOKENS", "MAX_OUTPUT_TOKENS", "MAX_OUTPUT_TOKEN", "LENGTH"}:
        return True
    return token.startswith("MAX_")


def _extract_usage_tokens(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return {}
    mapping = {
        "promptTokenCount": "prompt_tokens",
        "candidatesTokenCount": "completion_tokens",
        "totalTokenCount": "total_tokens",
        "cachedContentTokenCount": "cached_content_tokens",
    }
    usage_tokens: dict[str, int] = {}
    for source_key, target_key in mapping.items():
        value = usage.get(source_key)
        if isinstance(value, (int, float)):
            usage_tokens[target_key] = int(value)
    return usage_tokens


def _build_request_body(
    *,
    model: str,
    template: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    image_b64: str,
    schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "temperature": 0,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "responseSchema": schema,
    }
    thinking_budget = _resolve_thinking_budget(template, model)
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {
            "thinkingBudget": thinking_budget,
        }
    return {
        "system_instruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_b64,
                        }
                    },
                    {"text": user_prompt},
                ]
            }
        ],
        "generationConfig": generation_config,
    }


def _request_gemini_json(
    *,
    model: str,
    api_key: str,
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _api_url(model, api_key),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_raw = response.read().decode("utf-8")
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Gemini OCR timeout after {timeout:.0f}s") from exc
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw_detail = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            raw_detail = ""
        if raw_detail:
            try:
                parsed = json.loads(raw_detail)
            except Exception:  # noqa: BLE001
                parsed = None
            if isinstance(parsed, dict):
                error_payload = parsed.get("error")
                if isinstance(error_payload, dict):
                    status = str(error_payload.get("status") or "").strip()
                    message = str(error_payload.get("message") or "").strip()
                    code = error_payload.get("code")
                    if message:
                        detail = f"Gemini OCR HTTP {code or exc.code}"
                        if status:
                            detail += f" {status}"
                        detail += f": {message}"
            if not detail:
                detail = f"Gemini OCR HTTP {exc.code}: {raw_detail}"
        else:
            detail = f"Gemini OCR HTTP {exc.code}: {exc}"
        raise RuntimeError(detail) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise RuntimeError(f"Gemini OCR timeout after {timeout:.0f}s") from exc
        raise RuntimeError(f"Gemini OCR request failed: {exc}") from exc
    return json.loads(response_raw)


def run_gemini_ocr(
    *,
    pdf_bytes: bytes,
    template: dict,
    facility_id: str | None = None,
) -> dict[str, Any]:
    _ = facility_id
    api_key = _get_api_key()
    model = _get_model(template)
    page = max(int(template.get("page", 1)) - 1, 0) + 1
    resolution = int(template.get("gemini_ocr_resolution") or template.get("main_ocr_resolution") or 300)
    timeout = _resolve_timeout_seconds(template)
    max_tokens = int(template.get("gemini_ocr_max_tokens") or os.getenv("GEMINI_OCR_MAX_TOKENS", "12000"))
    retry_on_truncation = _as_bool(
        template.get("gemini_ocr_retry_on_truncation")
        if template.get("gemini_ocr_retry_on_truncation") is not None
        else os.getenv("GEMINI_OCR_RETRY_ON_TRUNCATION"),
        default=True,
    )
    retry_max_tokens = _safe_int(
        template.get("gemini_ocr_retry_max_tokens")
        or os.getenv("GEMINI_OCR_RETRY_MAX_TOKENS", "24000"),
        24000,
    )
    if retry_max_tokens < max_tokens:
        retry_max_tokens = max_tokens
    quantity_only_mode = _quantity_only_mode(template)
    full_table_mode = _full_table_mode(template)
    system_prompt = _build_prompt(template)
    user_prompt = _build_user_prompt(template)
    schema = _build_response_schema(template)
    row_fields = _row_fields(template)

    png_bytes = render_pdf_to_png_bytes(pdf_bytes=pdf_bytes, dpi=resolution, page=page)
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    attempts: list[dict[str, Any]] = []
    attempt_max_tokens = max_tokens
    parsed: dict[str, Any] | None = None
    text = ""
    recovered_truncated_json = False
    for attempt in range(1, 3):
        body = _build_request_body(
            model=model,
            template=template,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_b64=image_b64,
            schema=schema,
            max_tokens=attempt_max_tokens,
        )
        payload = _request_gemini_json(
            model=model,
            api_key=api_key,
            body=body,
            timeout=timeout,
        )
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("Gemini OCR returned empty response")
        finish_reason = _extract_finish_reason(payload)
        parsed, recovered_truncated_json = _extract_json_payload_with_meta(text)
        attempt_payload: dict[str, Any] = {
            "attempt": attempt,
            "max_output_tokens": int(attempt_max_tokens),
            "finish_reason": finish_reason,
            "response_chars": len(text),
            "recovered_truncated_json": bool(recovered_truncated_json),
        }
        usage_tokens = _extract_usage_tokens(payload)
        if usage_tokens:
            attempt_payload["usage"] = usage_tokens
        attempts.append(attempt_payload)
        if (
            attempt == 1
            and retry_on_truncation
            and _is_truncated_finish_reason(finish_reason)
            and retry_max_tokens > attempt_max_tokens
        ):
            attempt_max_tokens = retry_max_tokens
            continue
        break
    if parsed is None:
        raise RuntimeError("Gemini OCR returned empty response")
    parsed = _normalize_payload(
        parsed,
        row_fields=row_fields,
        quantity_only_mode=quantity_only_mode,
        full_table_mode=full_table_mode,
    )
    parsed.setdefault("facility_name", None)
    parsed.setdefault("date_strings", [])
    parsed.setdefault("rows", [])
    last_attempt = attempts[-1] if attempts else {}
    debug_payload: dict[str, Any] = {
        "provider": "gemini",
        "model": model,
        "page": page,
        "resolution": resolution,
        "response_chars": len(text),
        "attempt_count": len(attempts),
        "max_output_tokens": int(last_attempt.get("max_output_tokens") or attempt_max_tokens),
        "finish_reason": last_attempt.get("finish_reason"),
        "retry_on_truncation": bool(retry_on_truncation),
        "retry_max_tokens": int(retry_max_tokens),
        "retry_applied": bool(len(attempts) > 1),
        "recovered_truncated_json": bool(recovered_truncated_json),
        "quantity_only_mode": bool(quantity_only_mode),
        "full_table_mode": bool(full_table_mode),
    }
    if attempts:
        debug_payload["attempts"] = attempts
    if isinstance(last_attempt.get("usage"), dict):
        debug_payload["usage"] = last_attempt["usage"]
    returned_row_indexes = parsed.get("_ocr_returned_row_indexes")
    if isinstance(returned_row_indexes, list) and returned_row_indexes:
        debug_payload["returned_row_indexes"] = returned_row_indexes
    parsed["_ocr_raw_text"] = text
    parsed["_ocr_debug"] = debug_payload
    return parsed
