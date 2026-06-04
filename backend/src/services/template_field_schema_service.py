from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_QTY_FIELD_PATTERN = re.compile(r"^qty\.(?P<diet>[a-z0-9_]+)_(?P<area>[a-z0-9_]+)$")
_BLOCK_TOTAL_HEADER_TOKENS = {
    "total",
    "subtotal",
    "grandtotal",
    "grand_total",
    "blocktotal",
}
_SLOT_LABEL_HEADER_TOKENS = {
    "sub_category",
    "subcategory",
    "slot_label",
    "slotlabel",
    "menu_category",
}
_SLOT_LABEL_TRANSLATION = str.maketrans(
    {
        "Ａ": "A",
        "Ｂ": "B",
        "ａ": "A",
        "ｂ": "B",
        "１": "1",
        "２": "2",
        "①": "1",
        "②": "2",
        "（": "(",
        "）": ")",
        "　": " ",
    }
)


def _normalize_token(value: Any) -> str:
    return _TOKEN_PATTERN.sub("_", str(value or "").strip().lower()).strip("_")


def _normalize_area_token(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw in {"x", "common", "shared", "共通"}:
        return "x"
    if raw in {"2", "2f", "2階"}:
        return "2f"
    if raw in {"3", "3f", "3階"}:
        return "3f"
    return _normalize_token(raw) or "x"


def _parse_quantity_field_name(value: Any) -> tuple[str, str] | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    matched = _QTY_FIELD_PATTERN.match(token)
    if not matched:
        return None
    return matched.group("diet"), matched.group("area")


def canonical_aux_field_name(
    column: dict[str, Any] | None,
    *,
    fallback_index: int | None = None,
) -> str:
    source_index: int | None = None
    if isinstance(column, dict):
        try:
            source_index = int(column.get("index"))
        except Exception:
            source_index = None
    if source_index is None and fallback_index is not None and int(fallback_index) >= 0:
        source_index = int(fallback_index)
    if source_index is not None and source_index >= 0:
        return f"aux.col_{source_index}"
    return "aux.col"


def classify_aux_header_semantic(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = _normalize_token(raw)
    if "合計" in raw or "総計" in raw or "小計" in raw:
        return "block_total"
    if normalized in _BLOCK_TOTAL_HEADER_TOKENS:
        return "block_total"
    if normalized.endswith("_total") or normalized.endswith("total"):
        return "block_total"
    if "副区分" in raw or "副区" in raw:
        return "slot_label"
    if normalized in _SLOT_LABEL_HEADER_TOKENS:
        return "slot_label"
    return None


def _normalize_slot_label_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.translate(_SLOT_LABEL_TRANSLATION)
    text = text.replace("\n", "").replace("\r", "")
    text = re.sub(r"\s+", "", text)
    return text.upper()


def normalize_slot_label_display_value(value: Any) -> str:
    text = _normalize_slot_label_text(value)
    if not text:
        return ""
    if "主" in text or "主菜" in text or "主食" in text:
        if "B" in text:
            return "主Ｂ"
        if "A" in text:
            return "主Ａ"
        return "主"
    if "副" in text or "副菜" in text or "副食" in text or "副区" in text:
        if re.search(r"(^|[^0-9])2($|[^0-9])", text) or "②" in str(value or ""):
            return "副②"
        return "副①"
    compact = text.strip("()")
    if compact == "1":
        return "副①"
    if compact == "2":
        return "副②"
    return ""


def normalize_aux_semantic_display_value(value: Any, semantic: str | None) -> str:
    normalized_semantic = str(semantic or "").strip().lower()
    if normalized_semantic == "slot_label":
        return normalize_slot_label_display_value(value)
    text = str(value or "").strip()
    return text


def canonical_field_name_from_template_column(
    column: dict[str, Any] | None,
    *,
    fallback_index: int | None = None,
) -> str | None:
    if not isinstance(column, dict):
        return None
    role = str(column.get("role") or "").strip().lower()
    if role == "date":
        return "date_mmdd"
    if role == "daypart":
        return "daypart"
    if role == "menu_name":
        return "menu"
    if role == "note":
        return "remarks"
    if role == "aux":
        return canonical_aux_field_name(column, fallback_index=fallback_index)
    if role not in {"quantity", "quantity_change"}:
        return None
    parsed_name = _parse_quantity_field_name(column.get("name"))
    if parsed_name and bool(column.get("name_locked")):
        return str(column.get("name") or "").strip().lower()
    diet = _normalize_token(column.get("diet_type")) or (parsed_name[0] if parsed_name else "")
    area = _normalize_area_token(column.get("area_id")) or (parsed_name[1] if parsed_name else "x")
    if not diet:
        header = str(column.get("header") or "").strip()
        parsed_header = _parse_quantity_field_name(header)
        if parsed_header:
            diet = parsed_header[0]
            area = parsed_header[1]
    if not diet:
        return None
    return f"qty.{diet}_{area or 'x'}"


def derive_row_fields_from_columns(columns: Any) -> list[str]:
    if not isinstance(columns, list):
        return []
    ordered = sorted(
        [col for col in columns if isinstance(col, dict)],
        key=lambda col: int(col.get("index") or 0),
    )
    derived: list[str] = []
    seen: set[str] = set()
    for fallback_index, raw_col in enumerate(ordered):
        field = canonical_field_name_from_template_column(raw_col, fallback_index=fallback_index)
        if not field or field in seen:
            continue
        seen.add(field)
        derived.append(field)
    return derived


def build_header_by_field(columns: Any) -> dict[str, str]:
    if not isinstance(columns, list):
        return {}
    ordered = sorted(
        [col for col in columns if isinstance(col, dict)],
        key=lambda col: int(col.get("index") or 0),
    )
    headers: dict[str, str] = {}
    for fallback_index, raw_col in enumerate(ordered):
        field = canonical_field_name_from_template_column(raw_col, fallback_index=fallback_index)
        if not field or field in headers:
            continue
        header = str(raw_col.get("header") or "").strip() or field
        headers[field] = header
    return headers


def _normalize_field_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_field in value:
        field = str(raw_field or "").strip()
        if not field or field in seen:
            continue
        seen.add(field)
        normalized.append(field)
    return normalized


def derive_row_fields_from_template(template: Any) -> list[str]:
    if not isinstance(template, dict):
        return []
    derived = derive_row_fields_from_columns(template.get("columns"))
    if derived:
        return derived
    return _normalize_field_list(template.get("main_ocr_row_fields"))


def build_template_field_schema_contract(template: Any) -> dict[str, Any]:
    fields = derive_row_fields_from_template(template)
    if not fields:
        return {}
    base_contract = {
        "version": "v1",
        "fields": list(fields),
        "field_count": len(fields),
        "quantity_fields": [field for field in fields if field.startswith("qty.")],
        "aux_fields": [field for field in fields if field.startswith("aux.")],
        "remarks_present": "remarks" in fields,
    }
    fingerprint = hashlib.sha256(
        json.dumps(base_contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        **base_contract,
        "fingerprint": fingerprint,
    }
