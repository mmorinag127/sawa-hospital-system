import json
import os
import re
from copy import deepcopy
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from src.db import session_scope
from src.models.facility import Facility

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FACILITY_MASTER_PATH = Path(
    os.getenv("FACILITY_MASTER_PATH", DATA_DIR / "facility_master.template.json")
)
INGEST_POLICY_PATH = Path(os.getenv("INGEST_POLICY_PATH", DATA_DIR / "ingest_policy.template.json"))
FAX_TEMPLATE_REGISTRY_PATH = Path(
    os.getenv("FAX_TEMPLATE_REGISTRY_PATH", DATA_DIR / "fax_templates.yaml")
)

_NAME_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)
_CORP_TOKENS = [
    "株式会社",
    "有限会社",
    "合同会社",
    "医療法人社団",
    "医療法人財団",
    "医療法人",
    "社会福祉法人",
    "社会医療法人",
    "(株)",
    "（株）",
    "㈱",
    "㈲",
    "(有)",
    "（有）",
]
_PHONE_PATTERN = re.compile(r"(?:\+?81[-\s]?)?(?:0?\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4})")

_DEFAULT_ORDER_FORM_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern_id": "PATTERN_A",
        "label": "標準A",
        "description": "Placeholder pattern A (TBD)",
        "marker_cells": ["A1", "L1", "A40", "L40"],
    },
    {
        "pattern_id": "PATTERN_B",
        "label": "標準B",
        "description": "Placeholder pattern B (TBD)",
        "marker_cells": ["A1", "M1", "A42", "M42"],
    },
    {
        "pattern_id": "PATTERN_C",
        "label": "標準C",
        "description": "Placeholder pattern C (TBD)",
        "marker_cells": ["A1", "K1", "A38", "K38"],
    },
    {
        "pattern_id": "PATTERN_D",
        "label": "標準D",
        "description": "Placeholder pattern D (TBD)",
        "marker_cells": ["A1", "N1", "A44", "N44"],
    },
    {
        "pattern_id": "PATTERN_E",
        "label": "標準E",
        "description": "Placeholder pattern E (TBD)",
        "marker_cells": ["A1", "J1", "A36", "J36"],
    },
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pyyaml is required for fax template registry") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _default_fax_template_id_for_facility(
    facility_id: str | None,
    registry: dict[str, Any],
) -> str | None:
    if not facility_id or not isinstance(registry, dict) or not registry:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", str(facility_id).lower())
    if not normalized:
        return None
    matcher = re.compile(rf"^fax_{re.escape(normalized)}(?:_v(\d+))?$", re.IGNORECASE)
    candidates: list[tuple[int, str]] = []
    for template_id in registry.keys():
        key = str(template_id or "").strip()
        if not key:
            continue
        hit = matcher.match(key)
        if not hit:
            continue
        version = int(hit.group(1) or 0)
        candidates.append((version, key))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][1]


def _normalize_fax_template_ids(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


@lru_cache(maxsize=1)
def load_facility_master() -> dict:
    return _load_json(FACILITY_MASTER_PATH)


@lru_cache(maxsize=1)
def load_ingest_policy() -> dict:
    return _load_json(INGEST_POLICY_PATH)


@lru_cache(maxsize=1)
def load_fax_template_registry() -> dict:
    if not FAX_TEMPLATE_REGISTRY_PATH.exists():
        return {}
    data = _load_yaml(FAX_TEMPLATE_REGISTRY_PATH)
    templates = data.get("templates") if isinstance(data, dict) else None
    if isinstance(templates, dict):
        return templates
    if isinstance(data, dict):
        return data
    return {}


def reload_configs() -> None:
    load_facility_master.cache_clear()
    load_ingest_policy.cache_clear()
    load_fax_template_registry.cache_clear()


def _merge_template(base: Optional[dict], override: Optional[dict]) -> dict:
    if not base and not override:
        return {}
    result = deepcopy(base or {})
    if not override:
        return result
    for key, value in override.items():
        if key == "columns" and isinstance(value, list):
            # `columns` is a schema definition. Appending base+override creates
            # duplicated/contradicting fields and breaks OCR row mapping.
            # When override provides columns, treat it as authoritative.
            result["columns"] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_template(result.get(key), value)
        else:
            result[key] = value
    return result


def _facility_has_explicit_areas(facility: dict[str, Any]) -> bool:
    areas = facility.get("areas")
    if not isinstance(areas, list) or not areas:
        return False
    for area in areas:
        if isinstance(area, dict):
            area_id = str(area.get("area_id") or area.get("id") or "").strip()
            area_name = str(area.get("name") or "").strip()
            if area_id or area_name:
                return True
        else:
            if str(area or "").strip():
                return True
    return False


def _strip_area_suffix(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return text
    text = re.sub(r"\s*[0-9０-９]+\s*[FfＦｆ階]\s*$", "", text)
    return text.strip() or str(label or "").strip()


def _normalize_fax_template_for_area_mismatch(
    *,
    template: dict[str, Any],
    facility: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(template, dict):
        return template
    # Auto-collapsing area-specific quantity columns was causing destructive
    # schema changes for facilities with template-specific 2F/3F columns.
    # Keep original template unless explicitly opted in per facility.
    if not bool(facility.get("collapse_area_columns_when_no_areas", False)):
        return template
    if _facility_has_explicit_areas(facility):
        return template
    raw_columns = template.get("columns")
    if not isinstance(raw_columns, list) or not raw_columns:
        return template

    has_area_quantity = any(
        isinstance(col, dict)
        and str(col.get("role") or "").strip().lower() == "quantity"
        and str(col.get("area_id") or "").strip()
        for col in raw_columns
    )
    if not has_area_quantity:
        return template

    changed = False
    normalized_columns: list[dict[str, Any]] = []
    seen_quantity: set[str] = set()

    for raw_col in raw_columns:
        if not isinstance(raw_col, dict):
            continue
        col = deepcopy(raw_col)
        role = str(col.get("role") or "").strip().lower()
        if role == "quantity":
            diet_key = str(col.get("diet_type") or "").strip().lower()
            area_token = str(col.get("area_id") or "").strip()
            if area_token:
                col.pop("area_id", None)
                changed = True
                for key in ("name", "header", "label"):
                    if key in col:
                        col[key] = _strip_area_suffix(str(col.get(key) or ""))
            dedupe_key = diet_key or str(col.get("name") or col.get("header") or "")
            if dedupe_key and dedupe_key in seen_quantity:
                changed = True
                continue
            if dedupe_key:
                seen_quantity.add(dedupe_key)
        normalized_columns.append(col)

    if not changed:
        return template

    for idx, col in enumerate(normalized_columns):
        col["index"] = idx

    normalized = deepcopy(template)
    normalized["columns"] = normalized_columns
    if isinstance(normalized.get("main_ocr_row_fields"), list):
        rebuilt_fields: list[str] = []
        for col in normalized_columns:
            role = str(col.get("role") or "").strip().lower()
            if role == "date":
                rebuilt_fields.append("date_mmdd")
            elif role == "daypart":
                rebuilt_fields.append("daypart")
            elif role == "menu_name":
                rebuilt_fields.append("menu")
            elif role == "note":
                rebuilt_fields.append("remarks")
            elif role == "quantity":
                diet = str(col.get("diet_type") or "").strip() or "unknown"
                rebuilt_fields.append(f"qty.{diet}_x")
        if rebuilt_fields:
            normalized["main_ocr_row_fields"] = rebuilt_fields
    return normalized


def _normalize_field_diet_token(value: object) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[\s　]+", "", token)
    if not token:
        return "unknown"
    if ("bag" in token or "袋" in token) and (
        "regular" in token or "常食" in token or "通常" in token
    ):
        return "regular_bag"
    if ("soft" in token or "軟菜" in token or "やわ" in token) and (
        "mixer" in token or "ミキサ" in token
    ):
        return "soft_mixer"
    if "regular" in token or "常食" in token or "通常" in token:
        return "regular"
    if "soft" in token or "軟菜" in token or "やわ" in token:
        return "soft"
    if "mixer" in token or "ミキサ" in token:
        return "mixer"
    if "daycare" in token or "通所" in token:
        return "daycare"
    if "staff" in token or "職員" in token:
        return "staff"
    if "nomeat" in token or ("禁" in token and "肉" in token):
        return "no_meat"
    if "nofish" in token or ("禁" in token and "魚" in token):
        return "no_fish"
    if token in {"unknown", "none", "null", "na", "n/a", "不明", "なし"}:
        return "unknown"
    if token in {"change1", "変更1"}:
        return "change_1"
    if token in {"change2", "変更2"}:
        return "change_2"
    sanitized = re.sub(r"[^a-z0-9]+", "_", token).strip("_")
    return sanitized or "unknown"


def _is_explicit_unknown_marker(value: object) -> bool:
    token = str(value or "").strip().lower()
    token = re.sub(r"[\s　]+", "", token)
    return token in {"unknown", "none", "null", "na", "n/a", "不明", "なし"}


def _normalize_field_area_token(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "x"
    token = raw.translate(_NAME_TRANSLATION).lower()
    token = re.sub(r"[\s　]+", "", token)
    if not token:
        return "x"
    if re.fullmatch(r"\d+", token):
        return f"{token}f"
    match = re.search(r"(\d)(?:f|階)", token)
    if match:
        return f"{match.group(1)}f"
    if token in {"x", "all", "common", "共通", "none", "null", "na", "n/a", "なし"}:
        return "x"
    return "x"


def _default_header_for_role(role: str) -> str:
    if role == "date":
        return "日付"
    if role == "daypart":
        return "区分"
    if role == "menu_name":
        return "メニュー"
    if role == "note":
        return "備考"
    return ""


def _default_header_for_quantity(diet: str, area: str) -> str:
    diet_label = {
        "regular": "常食",
        "regular_bag": "常食(袋分け)",
        "soft": "軟菜",
        "soft_mixer": "軟菜/ミキサー",
        "mixer": "ミキサー",
        "daycare": "通所",
        "staff": "職員",
        "no_meat": "禁食(肉禁)",
        "no_fish": "禁食(魚禁)",
        "change_1": "変更1",
        "change_2": "変更2",
        "unknown": "不明",
    }.get(diet, diet)
    if area == "x":
        return diet_label
    return f"{diet_label}{area.upper()}"


def normalize_fax_template_columns(columns: Any) -> list[dict[str, Any]]:
    if not isinstance(columns, list):
        return []
    normalized: list[dict[str, Any]] = []
    ordered = sorted(
        [col for col in columns if isinstance(col, dict)],
        key=lambda col: int(col.get("index") or 0),
    )
    for idx, raw_col in enumerate(ordered):
        col = deepcopy(raw_col)
        role = str(col.get("role") or "").strip().lower()
        col["index"] = idx
        col["role"] = role
        header = str(col.get("header") or "").strip()
        name = str(col.get("name") or "").strip()
        if role == "quantity":
            label_token = header or name
            inferred_diet = _normalize_field_diet_token(label_token or col.get("diet_type"))
            explicit_diet = _normalize_field_diet_token(col.get("diet_type"))
            diet = inferred_diet if (header or name) else explicit_diet
            if (
                explicit_diet not in {"", "unknown"}
                and (
                    not label_token
                    or (
                        inferred_diet in {"", "unknown"}
                        and not _is_explicit_unknown_marker(label_token)
                    )
                )
            ):
                diet = explicit_diet
            raw_area = str(col.get("area_id") or "").strip()
            area = _normalize_field_area_token(raw_area)
            if area == "x":
                fallback_area = _normalize_field_area_token(header or name)
                if fallback_area != "x":
                    area = fallback_area
            col["diet_type"] = diet
            col["area_id"] = area.upper() if area != "x" else "X"
            if not header:
                col["header"] = _default_header_for_quantity(diet, area)
            if not name:
                col["name"] = f"qty.{diet}_{area}"
        else:
            if not header:
                default_header = _default_header_for_role(role)
                if default_header:
                    col["header"] = default_header
        normalized.append(col)
    return normalized


def _normalize_fax_template_columns_schema(template: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(template, dict):
        return template
    columns = template.get("columns")
    if not isinstance(columns, list):
        return template
    normalized = deepcopy(template)
    normalized["columns"] = normalize_fax_template_columns(columns)
    return normalized


def _derive_row_fields_from_columns(columns: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for col in sorted(columns, key=lambda item: int(item.get("index") or 0)):
        role = str(col.get("role") or "").strip().lower()
        if role == "date":
            fields.append("date_mmdd")
        elif role == "daypart":
            fields.append("daypart")
        elif role == "menu_name":
            fields.append("menu")
        elif role == "note":
            fields.append("remarks")
        elif role == "quantity":
            diet = _normalize_field_diet_token(col.get("diet_type"))
            area = _normalize_field_area_token(col.get("area_id"))
            fields.append(f"qty.{diet}_{area}")
    return fields


def _harmonize_main_ocr_row_fields(template: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(template, dict):
        return template
    columns = template.get("columns")
    if not isinstance(columns, list):
        return template
    normalized_columns = [col for col in columns if isinstance(col, dict)]
    if not normalized_columns:
        return template
    derived_fields = _derive_row_fields_from_columns(normalized_columns)
    if not derived_fields:
        return template
    existing_fields = template.get("main_ocr_row_fields")
    existing = (
        [str(item).strip() for item in existing_fields if str(item).strip()]
        if isinstance(existing_fields, list)
        else []
    )
    if existing == derived_fields:
        return template
    normalized = deepcopy(template)
    normalized["main_ocr_row_fields"] = derived_fields
    return normalized


def _normalize_order_form_patterns(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    patterns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pattern_id = str(item.get("pattern_id") or "").strip()
        if not pattern_id or pattern_id in seen:
            continue
        seen.add(pattern_id)
        normalized = dict(item)
        normalized["pattern_id"] = pattern_id
        patterns.append(normalized)
    return patterns


def get_order_form_patterns() -> list[dict[str, Any]]:
    master = load_facility_master()
    patterns = _normalize_order_form_patterns(master.get("order_form_patterns"))
    if patterns:
        return patterns
    return [dict(item) for item in _DEFAULT_ORDER_FORM_PATTERNS]


def get_order_form_pattern(pattern_id: str | None) -> Optional[dict[str, Any]]:
    if not pattern_id:
        return None
    target = pattern_id.strip()
    if not target:
        return None
    for pattern in get_order_form_patterns():
        if str(pattern.get("pattern_id") or "").strip() == target:
            return dict(pattern)
    return None


def _normalize_facility_text(value: str) -> str:
    if not value:
        return ""
    text = value.translate(_NAME_TRANSLATION).lower()
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[()（）\[\]【】]", "", text)
    for token in _CORP_TOKENS:
        text = text.replace(token, "")
    return text.strip()


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("81") and len(digits) >= 11:
        digits = digits[2:]
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def _phone_key(digits: str) -> str:
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def _extract_phone_numbers(text: str) -> list[str]:
    if not text:
        return []
    numbers = []
    for raw in _PHONE_PATTERN.findall(text):
        digits = _normalize_phone(raw)
        if len(digits) >= 10:
            numbers.append(_phone_key(digits))
    return sorted(set(numbers))


def _extract_text_lines(markdown: str) -> list[str]:
    if not markdown:
        return []
    lines: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("|"):
            continue
        if stripped.startswith("!["):
            continue
        if stripped.startswith("---"):
            continue
        lines.append(stripped)
    return lines


def _collect_facilities_for_match() -> list[dict]:
    master = load_facility_master()
    merged: dict[str, dict] = {}
    for fac in master.get("facilities", []):
        facility_id = str(fac.get("facility_id") or "").strip()
        if not facility_id:
            continue
        merged[facility_id] = {"facility_id": facility_id, **fac}
    with session_scope() as session:
        for fac in session.execute(select(Facility)).scalars().all():
            entry = merged.get(fac.id, {"facility_id": fac.id})
            if not entry.get("facility_name"):
                entry["facility_name"] = fac.name
            if fac.config and fac.config.config_json:
                entry.update(fac.config.config_json)
            merged[fac.id] = entry
    return list(merged.values())


def match_facility_candidates(
    ocr_text: str,
    *,
    min_auto: float = 0.92,
    min_suggest: float = 0.85,
) -> list[dict]:
    if not ocr_text:
        return []
    facilities = _collect_facilities_for_match()
    if not facilities:
        return []
    lines = _extract_text_lines(ocr_text)
    normalized_lines = [_normalize_facility_text(line) for line in lines]
    normalized_lines = [line for line in normalized_lines if line]
    phone_numbers = _extract_phone_numbers(ocr_text)

    exact_matches: list[dict] = []
    for fac in facilities:
        facility_id = fac.get("facility_id") or fac.get("id")
        facility_name = fac.get("facility_name") or fac.get("name") or ""
        aliases = fac.get("aliases") or []
        name_norm = _normalize_facility_text(str(facility_name))
        alias_norms = [_normalize_facility_text(str(alias)) for alias in aliases if alias]
        if name_norm and name_norm in normalized_lines:
            exact_matches.append(
                {
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "score": 1.0,
                    "reason": "name_exact",
                    "auto": True,
                }
            )
            continue
        if any(alias_norm and alias_norm in normalized_lines for alias_norm in alias_norms):
            exact_matches.append(
                {
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "score": 1.0,
                    "reason": "name_exact",
                    "auto": True,
                }
            )
    if exact_matches:
        return exact_matches

    phone_matches: list[dict] = []
    if phone_numbers:
        phone_keys = set(phone_numbers)
        for fac in facilities:
            facility_id = fac.get("facility_id") or fac.get("id")
            facility_name = fac.get("facility_name") or fac.get("name") or ""
            phones: list[str] = []
            for key in ("phone", "phone_number", "phone_numbers", "phones", "tel", "telephone", "fax"):
                value = fac.get(key)
                if isinstance(value, list):
                    phones.extend([str(item) for item in value if item])
                elif isinstance(value, str) and value.strip():
                    phones.append(value)
            normalized = [_phone_key(_normalize_phone(item)) for item in phones if item]
            if any(item in phone_keys for item in normalized if item):
                phone_matches.append(
                    {
                        "facility_id": facility_id,
                        "facility_name": facility_name,
                        "score": 1.0,
                        "reason": "phone",
                        "auto": True,
                    }
                )
    if phone_matches:
        return phone_matches

    fuzzy_matches: list[dict] = []
    for fac in facilities:
        facility_id = fac.get("facility_id") or fac.get("id")
        facility_name = fac.get("facility_name") or fac.get("name") or ""
        aliases = fac.get("aliases") or []
        name_norm = _normalize_facility_text(str(facility_name))
        alias_norms = [_normalize_facility_text(str(alias)) for alias in aliases if alias]
        candidates = [name_norm] + alias_norms
        best_ratio = 0.0
        for candidate in candidates:
            if not candidate:
                continue
            for line in normalized_lines:
                if not line:
                    continue
                if candidate == line:
                    best_ratio = 1.0
                    break
                if candidate in line or line in candidate:
                    best_ratio = max(best_ratio, 0.95)
                    continue
                ratio = SequenceMatcher(None, line, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
            if best_ratio == 1.0:
                break
        if best_ratio >= min_suggest:
            fuzzy_matches.append(
                {
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "score": round(best_ratio, 3),
                    "reason": "fuzzy",
                    "auto": best_ratio >= min_auto,
                }
            )
    fuzzy_matches.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return fuzzy_matches


def resolve_facility_id(facility_name: str) -> Optional[str]:
    target = facility_name.strip()
    with session_scope() as session:
        fac = session.execute(select(Facility).where(Facility.name == target)).scalars().first()
        if fac:
            return fac.id
    master = load_facility_master()
    for fac in master.get("facilities", []):
        if fac.get("facility_name") == target:
            return fac.get("facility_id")
        if target in (fac.get("aliases") or []):
            return fac.get("facility_id")
    return None


def get_facility_by_id(facility_id: str) -> Optional[dict]:
    with session_scope() as session:
        fac = session.get(Facility, facility_id)
        if fac:
            config = fac.config.config_json if fac.config else {}
            facility = {
                "facility_id": fac.id,
                "facility_name": fac.name,
                "areas": [
                    {"area_id": area.id, "name": area.name} for area in (fac.areas or [])
                ],
            }
            if config:
                facility.update(config)
            master = load_facility_master()
            for fac_master in master.get("facilities", []):
                if fac_master.get("facility_id") == facility_id:
                    for key, value in fac_master.items():
                        if key not in facility or facility[key] in (None, [], {}):
                            facility[key] = value
                    break
            return facility
    master = load_facility_master()
    for fac in master.get("facilities", []):
        if fac.get("facility_id") == facility_id:
            return fac
    return None


def get_facility_config(facility_id: str) -> Optional[dict[str, Any]]:
    master = load_facility_master()
    facility = get_facility_by_id(facility_id)
    if not facility:
        return None

    fax_template_override = facility.get("fax_template_override")
    fax_template = None
    registry = load_fax_template_registry()
    template_ids = _normalize_fax_template_ids(facility.get("fax_template_ids"))
    template_id = facility.get("fax_template_id")
    if isinstance(template_id, str):
        template_id = template_id.strip() or None
    if not template_id:
        template_id = _default_fax_template_id_for_facility(facility_id, registry)
    if template_id and template_id not in template_ids:
        template_ids.insert(0, template_id)
    if not template_id and template_ids:
        template_id = template_ids[0]
    if template_id:
        fax_template = registry.get(template_id)
    if not fax_template:
        fax_template = facility.get("fax_template")
    fax_template = _merge_template(master.get("fax_template_base"), fax_template)
    fax_template = _merge_template(fax_template, fax_template_override)
    fax_template = _normalize_fax_template_columns_schema(fax_template)
    fax_template = _normalize_fax_template_for_area_mismatch(
        template=fax_template,
        facility=facility,
    )
    fax_template = _harmonize_main_ocr_row_fields(fax_template)
    explicit_row_fields = (
        [str(item).strip() for item in fax_template_override.get("main_ocr_row_fields") if str(item).strip()]
        if isinstance(fax_template_override, dict)
        and isinstance(fax_template_override.get("main_ocr_row_fields"), list)
        else []
    )
    if explicit_row_fields:
        fax_template = deepcopy(fax_template)
        fax_template["main_ocr_row_fields"] = explicit_row_fields
    facility_prompt = (
        facility.get("main_ocr_facility_prompt")
        or facility.get("ocr_prompt")
        or facility.get("facility_prompt")
    )
    if isinstance(facility_prompt, str) and facility_prompt.strip():
        fax_template = deepcopy(fax_template)
        fax_template["main_ocr_facility_prompt"] = facility_prompt.strip()
    for key in (
        "main_ocr_provider",
        "openai_ocr_enabled",
        "openai_ocr_model",
        "openai_ocr_prompt",
        "openai_ocr_max_tokens",
        "openai_ocr_timeout_seconds",
        "openai_ocr_retry_on_truncation",
        "openai_ocr_retry_max_tokens",
        "openai_ocr_fallback_provider",
        "gemini_ocr_enabled",
        "gemini_ocr_model",
        "gemini_ocr_prompt",
        "gemini_ocr_max_tokens",
        "gemini_ocr_timeout_seconds",
        "gemini_ocr_retry_on_truncation",
        "gemini_ocr_retry_max_tokens",
        "gemini_ocr_fallback_provider",
        "large_cell_mode",
    ):
        if key in facility:
            fax_template = deepcopy(fax_template)
            fax_template[key] = facility.get(key)
    packaging_policy_override = facility.get("packaging_policy_override")
    packaging_policy = facility.get("packaging_policy") or _merge_template(
        master.get("packaging_policy_base"),
        packaging_policy_override,
    )
    label_profile_override = facility.get("label_profile_override")
    label_profile = facility.get("label_profile") or _merge_template(
        master.get("label_profile_base"),
        label_profile_override,
    )
    bag_types = facility.get("bag_types") or master.get("bag_types") or []
    bagging_exceptions = facility.get("bagging_exceptions") or []

    merged = {**facility}
    if template_id:
        merged["fax_template_id"] = template_id
    if template_ids:
        merged["fax_template_ids"] = template_ids
    merged["fax_template"] = fax_template
    merged["packaging_policy"] = packaging_policy
    merged["label_profile"] = label_profile
    merged["bag_types"] = bag_types
    merged["bagging_exceptions"] = bagging_exceptions
    pattern_id = merged.get("order_form_pattern_id")
    if isinstance(pattern_id, str) and pattern_id.strip():
        resolved_pattern_id = pattern_id.strip()
        merged["order_form_pattern_id"] = resolved_pattern_id
        pattern = get_order_form_pattern(resolved_pattern_id)
        if pattern:
            merged["order_form_pattern"] = pattern
    return merged
