from __future__ import annotations

import re
import unicodedata
from typing import Final


DIET_TYPE_LABELS: Final[dict[str, str]] = {
    "regular": "常食",
    "regular_bag": "常食(袋分け)",
    "soft": "軟菜",
    "soft_mixer": "軟菜/ミキサー",
    "mixer": "ミキサー",
    "daycare": "通所",
    "staff": "職員",
    "no_fried": "禁食(揚げ物禁)",
    "tea": "お茶",
    "business": "事業",
    "diabetes": "糖尿",
    "pregnancy": "妊娠",
    "sesame_allergy": "ゴマアレルギー",
    "no_meat": "禁食(肉禁)",
    "forbidden_other": "禁食(肉卵魚禁)",
    "no_fish": "禁食(魚禁)",
    "change_1": "変更1",
    "change_2": "変更2",
    "regular_1600kcal": "常食1600kcal",
    "soft_1600kcal": "軟菜1600kcal",
    "mixer_1600kcal": "ミキサー1600kcal",
    "1600kcal": "1600kcal",
    "unknown": "不明",
}

AGGREGATED_DIET_TYPE_LABELS: Final[dict[str, str]] = {
    "regular": "常食",
    "soft": "軟菜",
    "mixer": "ミキサー",
    "forbidden": "禁食",
    "tea": "お茶",
    "business": "事業",
    "diabetes": "糖尿",
    "pregnancy": "妊娠",
    "sesame_allergy": "ゴマアレルギー",
    "change_1": "変更1",
    "change_2": "変更2",
    "1600kcal": "1600kcal",
    "unknown": "不明",
}


def normalize_diet_type(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = unicodedata.normalize("NFKC", text)
    compact = (
        normalized.lower()
        .replace(" ", "")
        .replace("　", "")
        .replace("_", "")
        .replace("＿", "")
    )
    compact = re.sub(r"[／/・+＋\-]", "", compact)
    compact = re.sub(r"[()（）\[\]【】]", "", compact)
    if not compact:
        return None

    base: str | None = None
    if ("袋" in compact or "bag" in compact) and (
        "regular" in compact or "常食" in compact or "通常" in compact or compact == "常"
    ):
        base = "regular_bag"
    elif "regular" in compact or "常食" in compact or "通常" in compact:
        base = "regular"
    elif "daycare" in compact or "通所" in compact:
        base = "daycare"
    elif "staff" in compact or "職員" in compact:
        base = "staff"
    elif "nofried" in compact or "揚げ物禁" in normalized or "揚物禁" in normalized:
        base = "no_fried"
    elif "tea" in compact or "お茶" in compact:
        base = "tea"
    elif "business" in compact or "事業" in compact:
        base = "business"
    elif "diabetes" in compact or "diabetic" in compact or "糖尿" in compact:
        base = "diabetes"
    elif "pregnancy" in compact or "妊娠" in compact:
        base = "pregnancy"
    elif ("ごま" in compact or "ゴマ" in normalized or "sesame" in compact) and (
        "アレル" in normalized or "allergy" in compact
    ):
        base = "sesame_allergy"
    elif (
        ("肉" in compact or "meat" in compact)
        and ("卵" in compact or "玉子" in normalized or "egg" in compact)
        and ("魚" in compact or "鯖" in normalized or "さば" in normalized or "fish" in compact)
    ) or "肉卵魚禁" in normalized:
        base = "forbidden_other"
    elif "nomeat" in compact or "nobeef" in compact or "禁食肉禁" in compact or "肉禁" in compact:
        base = "no_meat"
    elif "nofish" in compact or "禁食魚禁" in compact or "魚禁" in compact:
        base = "no_fish"
    elif "change1" in compact or "変更1" in compact:
        base = "change_1"
    elif "change2" in compact or "変更2" in compact:
        base = "change_2"
    elif compact in {"unknown", "不明"}:
        base = "unknown"
    else:
        has_soft = "soft" in compact or "軟" in compact or "やわ" in compact
        has_mixer = "mixer" in compact or "mix" in compact or "ミキサ" in compact
        if has_soft and has_mixer:
            base = "soft_mixer"
        elif has_soft:
            base = "soft"
        elif has_mixer:
            base = "mixer"

    if "1600" in compact:
        if base in {"regular", "soft", "mixer"}:
            return f"{base}_1600kcal"
        if base is None:
            return "1600kcal"
    return base or text


def format_diet_type_label(value: object) -> str:
    normalized = normalize_diet_type(value)
    if not normalized:
        return "-"
    return DIET_TYPE_LABELS.get(normalized, str(value).strip() or "-")


def bucket_diet_type_for_aggregation(value: object) -> str | None:
    normalized = normalize_diet_type(value)
    if not normalized:
        return None
    if normalized in {"regular", "regular_bag", "daycare", "staff", "regular_1600kcal"}:
        return "regular"
    if normalized in {"soft", "soft_1600kcal"}:
        return "soft"
    if normalized in {"mixer", "mixer_1600kcal"}:
        return "mixer"
    if normalized in {"no_fried", "no_meat", "no_fish", "forbidden_other"}:
        return "forbidden"
    return normalized


def format_aggregated_diet_type_label(value: object) -> str:
    normalized = bucket_diet_type_for_aggregation(value)
    if not normalized:
        return "-"
    return AGGREGATED_DIET_TYPE_LABELS.get(normalized, str(value).strip() or "-")
