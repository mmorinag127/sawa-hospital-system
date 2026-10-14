import json
import os
import re
from copy import deepcopy
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from src.services import canonical_facility_store
from src.services.template_field_schema_service import derive_row_fields_from_columns


def get_authored_facility_config(facility_id: str, selected_template_id: str | None = None, *, master: dict | None = None) -> dict[str, Any]:
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution, current_workflow_execution, processing_start_configured_input
    if current_nonworkflow_execution() is not None or current_workflow_execution() is not None:
        config = processing_start_configured_input()["facility"]
        if config.get("facility_id") != facility_id:
            raise ValueError("configured_execution_facility_mismatch")
        return _validate_authored_facility_config(config, selected_template_id)
    source = load_facility_master() if master is None else master
    matches = [item for item in source.get("facilities", []) if isinstance(item, dict) and item.get("facility_id") == facility_id]
    if len(matches) != 1:
        raise ValueError("facility_template_ambiguous" if matches else "facility_not_found")
    config = deepcopy(matches[0])
    return _validate_authored_facility_config(config, selected_template_id)


def _validate_authored_facility_config(config: dict[str, Any], selected_template_id: str | None) -> dict[str, Any]:
    from src.services.config_validator import validate_facility_config
    from src.services.facility_template_version_service import validate_template_columns

    template = config.get("fax_template")
    if not isinstance(template, dict) or not template:
        raise ValueError("authored_fax_template_missing")
    columns = template.get("columns")
    if not isinstance(columns, list) or not columns or not all(isinstance(column, dict) for column in columns):
        raise ValueError("authored_fax_template_incomplete")
    validation = validate_facility_config({"fax_template": template})
    column_validation = validate_template_columns(columns)
    if validation["errors"] or column_validation["errors"]:
        raise ValueError("authored_fax_template_incomplete")
    declared_ids = _normalize_fax_template_ids(config.get("fax_template_ids"))
    declared_ids.extend(_normalize_fax_template_ids(config.get("fax_template_id")))
    if selected_template_id is None and len(set(declared_ids)) > 1:
        raise ValueError("authored_fax_template_ambiguous")
    if selected_template_id is not None and selected_template_id not in declared_ids:
        raise ValueError("authored_fax_template_id_mismatch")
    actual_template_id = template.get("template_id")
    if selected_template_id is not None and actual_template_id != selected_template_id:
        raise ValueError("authored_fax_template_id_mismatch")
    if declared_ids and (not isinstance(actual_template_id, str) or actual_template_id not in declared_ids):
        raise ValueError("authored_fax_template_id_mismatch")
    return config

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INGEST_POLICY_PATH = Path(os.getenv("INGEST_POLICY_PATH", DATA_DIR / "ingest_policy.template.json"))


def validate_selected_authored_facility_config(
    facility_id: str, selected_authored_version: dict[str, Any], *, master: dict | None = None,
) -> dict[str, Any]:
    """Read-only selected-version validation without menu or ingest-policy capture."""
    from src.services.workflow_attempt_guard_service import snapshots_equal

    source = load_facility_master() if master is None else master
    matches = [item for item in source.get("facilities", []) if isinstance(item, dict) and item.get("facility_id") == facility_id]
    if len(matches) != 1:
        raise ValueError("facility_template_ambiguous" if matches else "facility_not_found")
    versions = matches[0].get("versions")
    matching = [item for item in versions if isinstance(item, dict) and item.get("version_id") == selected_authored_version.get("version_id")] if isinstance(versions, list) else []
    from src.services.canonical_facility_store import selected_version_matches
    if len(matching) != 1 or not selected_version_matches(matching[0], selected_authored_version):
        raise ValueError("original_template_configured_source_mismatch")
    facility = _validate_authored_facility_config(deepcopy(selected_authored_version["facility"]), None)
    if facility.get("facility_id") != facility_id:
        raise ValueError("original_template_configured_source_mismatch")
    return facility


def capture_authored_processing_config(facility_id: str, *, selected_authored_version: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        master = load_facility_master()
        policy = _load_json(INGEST_POLICY_PATH)
    except (OSError, ValueError) as exc:
        raise ValueError("configured_source_unavailable") from exc
    matches = [item for item in master.get("facilities", []) if isinstance(item, dict) and item.get("facility_id") == facility_id]
    if len(matches) != 1:
        raise ValueError("facility_template_ambiguous" if matches else "facility_not_found")
    if selected_authored_version is not None:
        facility = validate_selected_authored_facility_config(facility_id, selected_authored_version, master=master)
    else:
        facility = _validate_authored_facility_config(deepcopy(matches[0]), None)
    if not isinstance(policy, dict):
        raise ValueError("ingest_policy_invalid")
    captured = {"facility": facility, "policy": policy}
    if selected_authored_version is not None:
        captured["authored_source_envelope"] = deepcopy(matches[0])
        captured["selected_authored_version"] = deepcopy(selected_authored_version)
    return captured


def captured_processing_config_matches(facility_id: str, captured: Any) -> bool:
    """Validate the saved selected source; the unversioned root is not authority."""
    from src.services.workflow_attempt_guard_service import snapshots_equal

    if (not isinstance(captured, dict)
            or set(captured) != {"facility", "policy", "authored_source_envelope", "selected_authored_version"}
            or not isinstance(captured.get("selected_authored_version"), dict)
            or not isinstance(captured.get("authored_source_envelope"), dict)):
        return False
    authored = captured["selected_authored_version"]
    envelope = captured["authored_source_envelope"]
    original_versions = envelope.get("versions")
    original_matches = [item for item in original_versions if isinstance(item, dict) and item.get("version_id") == authored.get("version_id")] if isinstance(original_versions, list) else []
    if (envelope.get("facility_id") != facility_id or len(original_matches) != 1
            or not snapshots_equal(original_matches[0], authored)):
        return False
    master = load_facility_master()
    matches = [item for item in master.get("facilities", []) if isinstance(item, dict) and item.get("facility_id") == facility_id]
    if len(matches) != 1 or not isinstance(matches[0].get("versions"), list):
        return False
    versions = [item for item in matches[0]["versions"] if isinstance(item, dict) and item.get("version_id") == authored.get("version_id")]
    from src.services.canonical_facility_store import selected_version_matches
    return (len(versions) == 1 and selected_version_matches(versions[0], authored)
            and isinstance(authored.get("facility"), dict)
            and authored["facility"].get("facility_id") == facility_id
            and snapshots_equal(captured.get("facility"), authored["facility"])
            and snapshots_equal(captured.get("policy"), _load_json(INGEST_POLICY_PATH)))

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

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def load_facility_master() -> dict:
    return canonical_facility_store.read_snapshot().master


def load_ingest_policy() -> dict:
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution, current_workflow_execution, processing_start_configured_input
    if current_nonworkflow_execution() is not None or current_workflow_execution() is not None:
        return processing_start_configured_input()["policy"]
    return _load_cached_ingest_policy()


@lru_cache(maxsize=1)
def _load_cached_ingest_policy() -> dict:
    return _load_json(INGEST_POLICY_PATH)


def reload_configs() -> None:
    _load_cached_ingest_policy.cache_clear()




def _normalize_cell_text(value: object) -> str:
    return str(value or "").strip().replace(" ", "").replace("　", "")








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


def _merge_authoritative_fax_override_metadata(
    target: dict[str, Any],
    *sources: Any,
) -> dict[str, Any]:
    """Keep non-column template semantics when columns are operator-authored."""

    for key in ("large_cell_mode", "body_merge_policy"):
        if key in target:
            continue
        for source in sources:
            if not isinstance(source, dict) or key not in source:
                continue
            target[key] = deepcopy(source.get(key))
            break
    return target


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
    return _merge_authoritative_fax_override_metadata(
        preserved,
        incoming_override,
        current_override,
        master_override,
    )


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
        normalized = _normalize_authoritative_fax_override(current_override)
        if normalized is not None:
            return _merge_authoritative_fax_override_metadata(
                normalized,
                current_override,
                master_override,
            )
        return None
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
    raise canonical_facility_store.CanonicalStoreError("legacy_facility_config_sanitizer_disabled", status_code=410)


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
            header_group = str(col.get("header_group") or "").strip()
            label_token = header or name
            diet_label_token = f"{header_group}{header}" if header_group and header else (header_group or label_token)
            has_header_group = bool(header_group)
            explicit_diet = _normalize_field_diet_token(explicit_diet_raw)
            diet_locked = bool(col.get("diet_type_locked", False) or col.get("diet_type_explicit", False))
            name_locked = bool(col.get("name_locked", False) or col.get("name_explicit", False))
            if diet_locked and explicit_diet_raw:
                diet = explicit_diet or explicit_diet_raw
            elif parsed_name:
                diet = parsed_name["diet"] or "unknown"
            elif has_header_group and explicit_diet not in {"", "unknown"}:
                diet = explicit_diet
            else:
                inferred_diet = _normalize_field_diet_token(diet_label_token or col.get("diet_type"))
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
                or (
                    not has_header_group
                    and _quantity_header_is_internal_token(header, diet=diet, area=area, name=name)
                )
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
    return _normalize_order_form_patterns(master.get("order_form_patterns"))


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
    return deepcopy([
        facility for facility in load_facility_master().get("facilities", [])
        if isinstance(facility, dict)
    ])


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
    matches = [
        facility for facility in load_facility_master().get("facilities", [])
        if isinstance(facility, dict) and (
            facility.get("facility_name") == target or target in (facility.get("aliases") or [])
        )
    ]
    return matches[0].get("facility_id") if len(matches) == 1 else None


def get_facility_by_id(facility_id: str, *, master: dict | None = None) -> Optional[dict[str, Any]]:
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution, current_workflow_execution

    if current_nonworkflow_execution() is not None or current_workflow_execution() is not None:
        return get_authored_facility_config(facility_id)
    source = load_facility_master() if master is None else master
    matches = [item for item in source.get("facilities", []) if isinstance(item, dict) and item.get("facility_id") == facility_id]
    if len(matches) > 1:
        raise ValueError("facility_template_ambiguous")
    return deepcopy(matches[0]) if matches else None


def get_facility_config(facility_id: str, *, master: dict | None = None) -> dict[str, Any]:
    if master is not None:
        facility = get_facility_by_id(facility_id, master=master)
        if facility is None:
            raise ValueError("facility_not_found")
        return facility
    return get_authored_facility_config(facility_id, master=master)


def get_facility_config_for_template(facility_id: str, template_id: str | None) -> dict[str, Any]:
    return get_authored_facility_config(facility_id, template_id)


def get_facility_config_for_date(facility_id: str, effective_date: object) -> dict:
    from src.services import facility_master_service

    resolved = facility_master_service.resolve_facility_for_date(facility_id, effective_date)
    # A version is complete. No root template merge, normalization, DB or current-config substitution.
    config = deepcopy(resolved["facility"])
    return {**resolved, "config": config}


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
