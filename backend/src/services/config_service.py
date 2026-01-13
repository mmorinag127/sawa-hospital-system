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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pyyaml is required for fax template registry") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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
            result.setdefault("columns", [])
            result["columns"] = result["columns"] + value
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_template(result.get(key), value)
        else:
            result[key] = value
    return result


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
    template_id = facility.get("fax_template_id")
    if template_id:
        registry = load_fax_template_registry()
        fax_template = registry.get(template_id)
    if not fax_template:
        fax_template = facility.get("fax_template")
    fax_template = _merge_template(master.get("fax_template_base"), fax_template)
    fax_template = _merge_template(fax_template, fax_template_override)
    facility_prompt = (
        facility.get("main_ocr_facility_prompt")
        or facility.get("ocr_prompt")
        or facility.get("facility_prompt")
    )
    if isinstance(facility_prompt, str) and facility_prompt.strip():
        fax_template = deepcopy(fax_template)
        fax_template["main_ocr_facility_prompt"] = facility_prompt.strip()
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

    merged = {**facility}
    merged["fax_template"] = fax_template
    merged["packaging_policy"] = packaging_policy
    merged["label_profile"] = label_profile
    return merged
