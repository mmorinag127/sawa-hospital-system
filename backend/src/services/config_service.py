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
from src.services.template_field_schema_service import derive_row_fields_from_columns

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
    raw = str(value or "").strip()
    if raw in {"-", "ー", "－"}:
        return "placeholder"
    token = raw.lower()
    token = re.sub(r"[\s　]+", "", token)
    if not token:
        return "unknown"
    if token in {"-", "placeholder", "blank", "spacer"}:
        return "placeholder"
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
    if "nofried" in token or "揚げ物禁" in str(value or "") or "揚物禁" in str(value or ""):
        return "no_fried"
    if (
        ("肉" in token or "meat" in token)
        and ("卵" in token or "玉子" in token or "egg" in token)
        and ("魚" in token or "鯖" in token or "さば" in token or "fish" in token)
    ) or "肉卵魚禁" in str(value or ""):
        return "forbidden_other"
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
    if token in {"花", "hana"} or "花" in token or "hana" in token:
        return "2f"
    if token in {"月", "tsuki"} or "月" in token or "tsuki" in token:
        return "3f"
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
        "diabetes": "糖尿",
        "soft": "軟菜",
        "soft_mixer": "軟菜/ミキサー",
        "mixer": "ミキサー",
        "daycare": "通所",
        "staff": "職員",
        "no_meat": "禁食(肉禁)",
        "forbidden_other": "禁食(肉卵魚禁)",
        "no_fish": "禁食(魚禁)",
        "no_fried": "揚げ物禁",
        "change_1": "変更1",
        "change_2": "変更2",
        "placeholder": "-",
        "unknown": "不明",
    }.get(diet, diet)
    if area == "x":
        return diet_label
    return f"{diet_label}{area.upper()}"


def _quantity_header_is_internal_token(
    header: str,
    *,
    diet: str,
    area: str,
    name: str,
) -> bool:
    raw = str(header or "").strip()
    if not raw:
        return True
    # Japanese/custom display labels are operator-facing labels; only rewrite
    # ASCII schema tokens that leaked from older template editors.
    if not re.fullmatch(r"[A-Za-z0-9_.\-\s]+", raw):
        return False
    token = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if not token:
        return False
    normalized_name = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    candidates = {
        diet,
        f"{diet}_{area}",
        f"qty_{diet}_{area}",
        normalized_name,
    }
    if area == "x":
        candidates.add(f"{diet}_x")
        candidates.add(f"qty_{diet}_x")
    return token in {candidate for candidate in candidates if candidate}


def _parse_quantity_field_name(value: object) -> dict[str, str] | None:
    raw = str(value or "").strip().lower()
    if not raw.startswith("qty."):
        return None
    body = re.sub(r"[^a-z0-9_]+", "_", raw[4:]).strip("_")
    if not body:
        return None
    raw_diet = body
    raw_area = "x"
    if "_" in body:
        candidate_diet, candidate_area = body.rsplit("_", 1)
        normalized_candidate_area = _normalize_field_area_token(candidate_area)
        if normalized_candidate_area != "x" or candidate_area == "x":
            raw_diet = candidate_diet
            raw_area = candidate_area
    diet = _normalize_field_diet_token(raw_diet)
    area = _normalize_field_area_token(raw_area)
    return {
        "diet": diet,
        "area": area,
        "field": f"qty.{raw_diet}_{raw_area}",
    }


def _quantity_signature(col: dict[str, Any]) -> tuple[str, str]:
    diet = _normalize_field_diet_token(col.get("diet_type") or col.get("name") or col.get("header"))
    area = _normalize_field_area_token(col.get("area_id") or "X")
    return diet, area


def _normalize_main_ocr_row_fields(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _fax_override_columns_are_authoritative(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("columns_authoritative"))


def _facility_template_source(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("facility_template_source")
        or value.get("fax_template_source")
        or ""
    ).strip().lower()


def _facility_template_operator_override_enabled(value: Any) -> bool:
    return _facility_template_source(value) in {"operator_override", "facility_override", "db_override"}


def _normalize_authoritative_fax_override(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized = deepcopy(value)
    normalized_columns = normalize_fax_template_columns(value.get("columns"))
    if normalized_columns:
        normalized["columns"] = normalized_columns
        normalized["main_ocr_row_fields"] = derive_row_fields_from_columns(normalized_columns)
    elif "columns" in normalized:
        normalized.pop("columns", None)
        normalized.pop("main_ocr_row_fields", None)
    if not normalized:
        return None
    return normalized


def _preserve_authoritative_fax_override(
    current_override: Any,
    incoming_override: Any,
    master_override: Any,
    *,
    allow_authoritative_column_changes: bool,
) -> dict[str, Any] | None:
    if allow_authoritative_column_changes:
        if isinstance(incoming_override, dict):
            return deepcopy(incoming_override)
        return None

    authoritative_source = None
    if _fax_override_columns_are_authoritative(current_override):
        authoritative_source = current_override
    elif _fax_override_columns_are_authoritative(master_override):
        authoritative_source = master_override

    if not isinstance(authoritative_source, dict):
        if isinstance(incoming_override, dict):
            return deepcopy(incoming_override)
        return None

    normalized_authoritative = _normalize_authoritative_fax_override(authoritative_source)
    if normalized_authoritative is None:
        if isinstance(incoming_override, dict):
            return deepcopy(incoming_override)
        return None

    if not isinstance(incoming_override, dict):
        if _fax_override_columns_are_authoritative(current_override):
            return normalized_authoritative
        return None

    preserved = deepcopy(incoming_override)
    if normalized_authoritative.get("columns"):
        preserved["columns"] = deepcopy(normalized_authoritative["columns"])
    if normalized_authoritative.get("main_ocr_row_fields"):
        preserved["main_ocr_row_fields"] = deepcopy(normalized_authoritative["main_ocr_row_fields"])
    preserved["columns_authoritative"] = True
    return preserved


def _should_prefer_master_fax_override(
    current_override: Any,
    master_override: Any,
) -> bool:
    if _fax_override_columns_are_authoritative(current_override):
        return False
    if not isinstance(current_override, dict) or not isinstance(master_override, dict):
        return False
    current_columns = normalize_fax_template_columns(current_override.get("columns"))
    master_columns = normalize_fax_template_columns(master_override.get("columns"))
    if not current_columns or not master_columns:
        return False
    current_qty = [_quantity_signature(col) for col in current_columns if str(col.get("role") or "").strip().lower() == "quantity"]
    master_qty = [_quantity_signature(col) for col in master_columns if str(col.get("role") or "").strip().lower() == "quantity"]
    if current_qty and master_qty:
        current_qty_set = set(current_qty)
        master_qty_set = set(master_qty)
        if current_qty_set < master_qty_set:
            # A non-authoritative persisted override that only contains a subset
            # of the master quantity schema is stale. Operator-authored
            # reductions are stored with columns_authoritative=True and are
            # handled above.
            return True
    current_aux = [col for col in current_columns if str(col.get("role") or "").strip().lower() == "aux"]
    master_aux = [col for col in master_columns if str(col.get("role") or "").strip().lower() == "aux"]
    if not master_aux or len(current_aux) >= len(master_aux):
        return False

    current_menu_index = next(
        (int(col.get("index") or 0) for col in current_columns if str(col.get("role") or "").strip().lower() == "menu_name"),
        None,
    )
    master_menu_index = next(
        (int(col.get("index") or 0) for col in master_columns if str(col.get("role") or "").strip().lower() == "menu_name"),
        None,
    )
    if current_menu_index is None or master_menu_index is None or current_menu_index >= master_menu_index:
        return False

    if not current_qty or not master_qty:
        return False
    if set(current_qty) != set(master_qty):
        return False

    # Typical stale shape: master keeps the omitted aux gap while the persisted override stays
    # left-shifted. Even when the quantity family list matches, the missing aux column still
    # makes the persisted override structurally wrong for OCR source indexes.
    return True


def _reconcile_fax_template_override(
    current_override: Any,
    master_override: Any,
    *,
    drop_redundant: bool,
    prefer_master: bool = False,
) -> dict[str, Any] | None:
    if prefer_master and isinstance(master_override, dict):
        normalized_master = _normalize_authoritative_fax_override(master_override)
        if normalized_master is not None:
            merged = deepcopy(current_override) if isinstance(current_override, dict) else {}
            merged.update(normalized_master)
            return merged
    if not isinstance(current_override, dict):
        return None
    if _fax_override_columns_are_authoritative(current_override):
        return _normalize_authoritative_fax_override(current_override)
    if _fax_override_columns_are_authoritative(master_override):
        preserved = _preserve_authoritative_fax_override(
            current_override,
            current_override,
            master_override,
            allow_authoritative_column_changes=False,
        )
        return _normalize_authoritative_fax_override(preserved)
    if not isinstance(master_override, dict):
        return deepcopy(current_override)

    reconciled = deepcopy(current_override)
    current_columns = normalize_fax_template_columns(current_override.get("columns"))
    master_columns = normalize_fax_template_columns(master_override.get("columns"))
    current_fields = _normalize_main_ocr_row_fields(current_override.get("main_ocr_row_fields"))
    master_fields = _normalize_main_ocr_row_fields(master_override.get("main_ocr_row_fields"))
    master_authoritative = _fax_override_columns_are_authoritative(master_override)
    if not master_fields and master_columns:
        master_fields = derive_row_fields_from_columns(master_columns)

    columns_identical = bool(current_columns and master_columns and current_columns == master_columns)
    prefer_master = _should_prefer_master_fax_override(current_override, master_override)

    if columns_identical or prefer_master:
        if master_columns:
            if drop_redundant and columns_identical and not prefer_master and not master_authoritative:
                reconciled.pop("columns", None)
            else:
                reconciled["columns"] = deepcopy(master_columns)

        if master_fields:
            if current_fields != master_fields:
                reconciled["main_ocr_row_fields"] = deepcopy(master_fields)
            elif drop_redundant:
                reconciled.pop("main_ocr_row_fields", None)
        if master_authoritative:
            reconciled["columns_authoritative"] = True

    if not reconciled:
        return None
    return reconciled


def sanitize_facility_config_for_storage(
    facility_id: str,
    config: dict[str, Any],
    *,
    current_config: dict[str, Any] | None = None,
    allow_authoritative_column_changes: bool = False,
) -> dict[str, Any]:
    sanitized = deepcopy(config)
    master_override = None
    master = load_facility_master()
    for fac_master in master.get("facilities", []):
        if fac_master.get("facility_id") == facility_id:
            master_override = fac_master.get("fax_template_override")
            break

    current_override = (current_config or {}).get("fax_template_override")
    operator_template_source = _facility_template_operator_override_enabled(
        sanitized,
    ) or _facility_template_operator_override_enabled(current_config)
    if operator_template_source and not _facility_template_source(sanitized):
        source = _facility_template_source(current_config)
        if source:
            sanitized["facility_template_source"] = source
    fax_override = _preserve_authoritative_fax_override(
        current_override,
        sanitized.get("fax_template_override"),
        master_override,
        allow_authoritative_column_changes=allow_authoritative_column_changes,
    )
    if fax_override is None:
        sanitized.pop("fax_template_override", None)
        return sanitized

    reconciled = _reconcile_fax_template_override(
        fax_override,
        master_override,
        drop_redundant=True,
        prefer_master=bool(master_override) and not operator_template_source,
    )
    if reconciled is None:
        sanitized.pop("fax_template_override", None)
    else:
        sanitized["fax_template_override"] = reconciled
    return sanitized


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
        requested_role = str(col.get("role") or "").strip().lower()
        header = str(col.get("header") or "").strip()
        name = str(col.get("name") or "").strip()
        source_index_raw = col.get("source_index")
        try:
            source_index = int(source_index_raw) if source_index_raw is not None else None
        except Exception:
            source_index = None
        parsed_name = _parse_quantity_field_name(name)
        explicit_diet_raw = str(col.get("diet_type") or "").strip()
        explicit_area_raw = str(col.get("area_id") or "").strip()
        role = requested_role
        if requested_role != "quantity" and (
            parsed_name
            or explicit_diet_raw
            or explicit_area_raw
        ):
            role = "quantity"
        col["index"] = idx
        if source_index is not None and source_index >= 0:
            col["source_index"] = source_index
        else:
            col.pop("source_index", None)
        col["role"] = role
        if role == "quantity":
            label_token = header or name
            explicit_diet = _normalize_field_diet_token(explicit_diet_raw)
            diet_locked = bool(col.get("diet_type_locked", False) or col.get("diet_type_explicit", False))
            name_locked = bool(col.get("name_locked", False) or col.get("name_explicit", False))
            if diet_locked and explicit_diet_raw:
                diet = explicit_diet or explicit_diet_raw
            elif parsed_name:
                diet = parsed_name["diet"] or "unknown"
            else:
                inferred_diet = _normalize_field_diet_token(label_token or col.get("diet_type"))
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
            legacy_unknown_spacer = (
                diet == "unknown"
                and _is_explicit_unknown_marker(label_token or explicit_diet_raw)
            )
            if legacy_unknown_spacer:
                diet = "placeholder"
            raw_area = str(col.get("area_id") or "").strip()
            area_locked = bool(col.get("area_id_locked", False) or col.get("area_id_explicit", False))
            if area_locked and raw_area:
                area = _normalize_field_area_token(raw_area)
            elif parsed_name:
                area = parsed_name["area"] or "x"
            else:
                area = _normalize_field_area_token(raw_area)
                if area == "x":
                    fallback_area = _normalize_field_area_token(header or name)
                    if fallback_area != "x":
                        area = fallback_area
            col.pop("diet_type_explicit", None)
            col.pop("area_id_explicit", None)
            col.pop("name_explicit", None)
            if diet_locked:
                col["diet_type_locked"] = True
            else:
                col.pop("diet_type_locked", None)
            if area_locked:
                col["area_id_locked"] = True
            else:
                col.pop("area_id_locked", None)
            if name_locked:
                col["name_locked"] = True
            else:
                col.pop("name_locked", None)
            col["diet_type"] = diet
            col["area_id"] = area.upper() if area != "x" else "X"
            if (
                not header
                or legacy_unknown_spacer
                or _quantity_header_is_internal_token(header, diet=diet, area=area, name=name)
            ):
                col["header"] = _default_header_for_quantity(diet, area)
            if name_locked and parsed_name:
                col["name"] = parsed_name["field"]
            else:
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
    return derive_row_fields_from_columns(columns)


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
                    prefer_master_template = not _facility_template_operator_override_enabled(facility)
                    if prefer_master_template:
                        for key in ("fax_template_id", "fax_template_ids", "fax_template_override"):
                            value = fac_master.get(key)
                            if value not in (None, [], {}):
                                facility[key] = deepcopy(value)
                    master_override = fac_master.get("fax_template_override")
                    current_override = facility.get("fax_template_override")
                    reconciled_override = _reconcile_fax_template_override(
                        current_override,
                        master_override,
                        drop_redundant=False,
                        prefer_master=prefer_master_template and bool(master_override),
                    )
                    if reconciled_override is None:
                        facility.pop("fax_template_override", None)
                    else:
                        facility["fax_template_override"] = reconciled_override
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


def _build_facility_config(
    *,
    facility_id: str,
    facility: dict[str, Any],
    selected_template_id: str | None = None,
) -> dict[str, Any]:
    master = load_facility_master()
    fax_template_override = facility.get("fax_template_override")
    fax_template = None
    registry = load_fax_template_registry()
    template_ids = _normalize_fax_template_ids(facility.get("fax_template_ids"))
    template_id = selected_template_id if isinstance(selected_template_id, str) else facility.get("fax_template_id")
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
    if explicit_row_fields and not bool(fax_template.get("columns_authoritative")):
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
        "quantity_assignment_strategy",
        "hakodate_header_rows",
        "hakodate_data_row_count",
        "hakodate_ocr_resolution",
        "hakodate_min_edge_margin_ratio",
        "hakodate_template_signature",
        "hakodate_template_signature_components",
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


def get_facility_config(facility_id: str) -> Optional[dict[str, Any]]:
    facility = get_facility_by_id(facility_id)
    if not facility:
        return None
    return _build_facility_config(
        facility_id=facility_id,
        facility=facility,
    )


def get_facility_config_for_template(
    facility_id: str,
    template_id: str | None,
) -> Optional[dict[str, Any]]:
    facility = get_facility_by_id(facility_id)
    if not facility:
        return None
    return _build_facility_config(
        facility_id=facility_id,
        facility=facility,
        selected_template_id=template_id,
    )


def _effective_fax_template_signature(template: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not isinstance(template, dict):
        return None
    columns = normalize_fax_template_columns(template.get("columns"))
    normalized_columns: list[tuple[Any, ...]] = []
    for idx, raw_col in enumerate(columns):
        if not isinstance(raw_col, dict):
            continue
        role = str(raw_col.get("role") or "").strip().lower()
        header = str(raw_col.get("header") or raw_col.get("name") or raw_col.get("label") or "").strip()
        index = int(raw_col.get("index") or idx)
        if role == "quantity":
            normalized_columns.append(
                (
                    index,
                    role,
                    _normalize_field_diet_token(raw_col.get("diet_type") or raw_col.get("name") or header),
                    _normalize_field_area_token(raw_col.get("area_id") or "X"),
                    header,
                )
            )
            continue
        normalized_columns.append(
            (
                index,
                role,
                str(raw_col.get("format") or "").strip(),
                header,
            )
        )
    row_fields = tuple(_normalize_main_ocr_row_fields(template.get("main_ocr_row_fields")))
    return (
        tuple(normalized_columns),
        row_fields,
    )


def collapse_equivalent_template_ids(
    facility_id: str | None,
    template_ids: Any,
) -> list[str]:
    normalized_facility_id = str(facility_id or "").strip()
    normalized_template_ids = _normalize_fax_template_ids(template_ids)
    if not normalized_facility_id or len(normalized_template_ids) <= 1:
        return normalized_template_ids

    collapsed: list[str] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for template_id in normalized_template_ids:
        facility_config = get_facility_config_for_template(normalized_facility_id, template_id)
        fax_template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
        signature = _effective_fax_template_signature(
            fax_template if isinstance(fax_template, dict) else None
        )
        if signature is None:
            collapsed.append(template_id)
            continue
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        collapsed.append(template_id)
    return collapsed
