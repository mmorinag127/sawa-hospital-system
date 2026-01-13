from io import BytesIO
import re
from loguru import logger
from uuid import uuid4
import pandas as pd

from sqlalchemy import delete, select

from src.db import session_scope
from src.models.menu import WeeklyMenu, MenuItem, MenuMaster, MenuFacilityOverride
from src.services.notification_service import record_event


MEAL_SLOT_TERMS = {
    "朝",
    "昼",
    "夕",
    "朝食",
    "昼食",
    "夕食",
    "朝アサ",
    "昼ヒル",
    "夕ユウ",
    "am",
    "pm",
}

HEADER_SKIP_TERMS = {
    "区分",
    "日付",
    "曜日",
    "日",
    "date",
    "day",
    "time",
    "時間帯",
}

_MENU_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)

_MASTER_FIELDS = (
    "unit_type",
    "qty_per_serving",
    "temp_type",
    "daypart",
    "category",
    "bag_max_qty",
    "bag_max_unit",
)


def _resolve_menu_name_column(columns: list[str]) -> str:
    for candidate in ["menu", "メニュー", "品名", "商品名", "料理名", "献立"]:
        for col in columns:
            if candidate.lower() in col.lower():
                return col
    return columns[0] if columns else "menu"


def _normalize_menu_name(value: str) -> str:
    if not value:
        return ""
    normalized = value.translate(_MENU_TRANSLATION)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("・", "").replace("／", "/")
    return normalized.strip().lower()


def _is_blank_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_cell_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def _normalize_menu_value(value: object) -> str:
    if isinstance(value, (int, float)) and float(value) == 0.0:
        return ""
    text = _normalize_cell_value(value)
    if not text:
        return ""
    compact = text.replace("．", ".")
    if compact in {"0", "０", "0.0", "0.00"}:
        return ""
    try:
        if float(compact) == 0.0:
            return ""
    except Exception:
        pass
    return text


def _is_date_value(value: str) -> bool:
    if not value:
        return False
    if value.isdigit() and len(value) in {6, 7, 8}:
        return True
    if "/" in value or "-" in value:
        parts = value.replace("-", "/").split("/")
        if all(part.isdigit() for part in parts if part):
            return True
    return False


def _is_meal_slot_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if normalized in MEAL_SLOT_TERMS:
        return True
    return False


def _is_menu_category_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if any(token in normalized for token in ("副菜", "主菜", "添え", "副①", "副②", "主Ａ", "主A")):
        return True
    return False


def _find_product_name_columns(columns: list[str]) -> list[str]:
    matches: list[str] = []
    for col in columns:
        header = str(col)
        if any(token in header for token in ("商品名", "品名", "料理名", "献立")):
            matches.append(col)
    return matches


def _build_master_defaults(
    session,
    names: list[str],
    facility_id: str | None = None,
) -> dict[str, dict]:
    normalized_names = {_normalize_menu_name(name): name for name in names if name}
    normalized_keys = [key for key in normalized_names.keys() if key]
    if not normalized_keys:
        return {}
    masters = (
        session.execute(
            select(MenuMaster).where(MenuMaster.normalized_name.in_(normalized_keys))
        )
        .scalars()
        .all()
    )
    master_by_norm = {master.normalized_name: master for master in masters}
    overrides: dict[str, MenuFacilityOverride] = {}
    if facility_id and masters:
        master_ids = [master.id for master in masters]
        override_rows = (
            session.execute(
                select(MenuFacilityOverride)
                .where(MenuFacilityOverride.facility_id == facility_id)
                .where(MenuFacilityOverride.menu_master_id.in_(master_ids))
            )
            .scalars()
            .all()
        )
        overrides = {row.menu_master_id: row for row in override_rows}

    defaults: dict[str, dict] = {}
    for normalized in normalized_keys:
        master = master_by_norm.get(normalized)
        if not master:
            continue
        override = overrides.get(master.id) if facility_id else None
        payload: dict[str, object] = {}
        for field in _MASTER_FIELDS:
            value = None
            if override is not None:
                override_value = getattr(override, field)
                if not _is_blank_value(override_value):
                    value = override_value
            if value is None:
                master_value = getattr(master, field)
                if not _is_blank_value(master_value):
                    value = master_value
            if value is not None:
                payload[field] = value
        defaults[normalized] = payload
    return defaults


def _merge_master_defaults(items: list[dict], facility_id: str | None = None) -> list[dict]:
    names = [item.get("name") or "" for item in items]
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    if not defaults:
        return items
    merged: list[dict] = []
    for item in items:
        normalized = _normalize_menu_name(item.get("name") or "")
        payload = dict(item)
        defaults_for_item = defaults.get(normalized)
        if defaults_for_item:
            for field, value in defaults_for_item.items():
                if _is_blank_value(payload.get(field)):
                    payload[field] = value
            for field in ("bag_max_qty", "bag_max_unit"):
                payload.setdefault(field, defaults_for_item.get(field))
        merged.append(payload)
    return merged


def _infer_menu_name_column(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    best_col = None
    best_score = -1
    for col in df.columns:
        header = str(col).strip()
        header_lower = header.lower()
        if any(term in header for term in HEADER_SKIP_TERMS) or any(
            term in header_lower for term in HEADER_SKIP_TERMS
        ):
            continue
        series = df[col]
        values = [_normalize_menu_value(v) for v in series]
        values = [v for v in values if v]
        if not values:
            continue
        meaningful = [
            v
            for v in values
            if not _is_meal_slot_value(v) and not _is_date_value(v) and not _is_menu_category_value(v)
        ]
        if not meaningful:
            continue
        unique_count = len(set(meaningful))
        score = unique_count
        if score > best_score:
            best_col = col
            best_score = score
    return str(best_col) if best_col is not None else None


def _load_menu_frame(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None,
    header: int | None = 0,
) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(BytesIO(file_bytes), header=header)
    if sheet_name:
        return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name, header=header)
    xls = pd.ExcelFile(BytesIO(file_bytes))
    if "メニュー" in xls.sheet_names:
        return xls.parse("メニュー", header=header)
    return xls.parse(xls.sheet_names[0], header=header)


def _has_known_header(columns: list[str]) -> bool:
    tokens = ("menu", "メニュー", "品名", "商品名", "料理名", "献立")
    for col in columns:
        if any(token.lower() in col.lower() for token in tokens):
            return True
    return False


def _is_header_like_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if any(term in normalized for term in HEADER_SKIP_TERMS):
        return True
    if any(token in normalized for token in ("発注", "連絡表", "施設名", "締切", "ご記入", "禁食", "備考", "変更", "※")):
        return True
    return False


def _is_non_menu_value(value: str) -> bool:
    if not value:
        return True
    if _is_date_value(value) or _is_meal_slot_value(value) or _is_menu_category_value(value):
        return True
    if _is_header_like_value(value):
        return True
    return False


def _extract_menu_names(file_bytes: bytes, filename: str, sheet_name: str | None) -> list[str]:
    try:
        df = _load_menu_frame(file_bytes, filename, sheet_name)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid menu file") from exc
    if df.empty or not df.columns.tolist():
        raise ValueError("menu file is empty")
    columns = [str(c) for c in df.columns]
    if not _has_known_header(columns) and any(col.lower().startswith("unnamed") for col in columns):
        df = _load_menu_frame(file_bytes, filename, sheet_name, header=None)
        header_row = None
        for idx, row in df.iterrows():
            values = [str(v) for v in row.tolist() if not _is_blank_value(v)]
            if any("献立" in value or "メニュー" in value or "商品名" in value or "品名" in value for value in values):
                header_row = idx
                break
        if header_row is not None:
            header_values = [str(v).strip() for v in df.iloc[header_row].tolist()]
            df = df.iloc[header_row + 1 :].copy()
            df.columns = header_values
            columns = [str(c) for c in df.columns]
    product_cols = _find_product_name_columns(columns)
    if product_cols:
        names: list[str] = []
        seen: set[str] = set()
        for _, row in df.iterrows():
            for col in product_cols:
                name = _normalize_menu_value(row.get(col, ""))
                if not name or _is_non_menu_value(name) or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        if names:
            return names
    name_col = _resolve_menu_name_column(columns)
    if name_col:
        series = df[name_col] if name_col in df.columns else None
        if series is not None:
            values = [_normalize_menu_value(v) for v in series]
            values = [v for v in values if v]
            if values:
                meal_slots = sum(1 for v in values if _is_meal_slot_value(v))
                category_slots = sum(1 for v in values if _is_menu_category_value(v))
                if meal_slots / len(values) >= 0.6 or category_slots / len(values) >= 0.6:
                    inferred = _infer_menu_name_column(df)
                    if inferred:
                        name_col = inferred
    if not name_col:
        name_col = _infer_menu_name_column(df) or (columns[0] if columns else "menu")
    names: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        name = _normalize_menu_value(row.get(name_col, ""))
        if not name or _is_non_menu_value(name) or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        raise ValueError("no menu items found")
    return names


def create_menu(week_id: str, file_bytes: bytes, filename: str, sheet_name: str | None = None):
    names = _extract_menu_names(file_bytes, filename, sheet_name)
    with session_scope() as session:
        replaced = False
        menu = session.get(WeeklyMenu, week_id)
        defaults = _build_master_defaults(session, names)
        if menu:
            replaced = True
            menu.filename = filename
            session.execute(delete(MenuItem).where(MenuItem.weekly_menu_id == week_id))
        else:
            menu = WeeklyMenu(id=week_id, filename=filename)
            session.add(menu)
            session.flush()
            session.refresh(menu)
        for name in names:
            default = defaults.get(_normalize_menu_name(name), {})
            item_id = f"MEI{uuid4().hex[:6]}"
            session.add(
                MenuItem(
                    id=item_id,
                    weekly_menu_id=week_id,
                    name=name,
                    unit_type=default.get("unit_type"),
                    qty_per_serving=default.get("qty_per_serving"),
                    temp_type=default.get("temp_type"),
                    daypart=default.get("daypart"),
                    category=default.get("category"),
                )
            )
        item_count = len(names)
        logger.info(
            "Menu uploaded",
            week_id=week_id,
            filename=filename,
            replaced=replaced,
            item_count=item_count,
        )
        record_event(
            "menu_upload",
            actor="system",
            target=week_id,
            wek=week_id,
            metadata={"filename": filename, "replaced": replaced, "item_count": item_count},
        )
        return serialize_menu(menu), replaced, item_count


def update_item(week_id: str, item_id: str, body: dict) -> bool:
    with session_scope() as session:
        item = session.get(MenuItem, item_id)
        if not item:
            return False
        for key in ["unit_type", "qty_per_serving", "temp_type", "daypart", "category", "facility_override", "name"]:
            if key in body:
                setattr(item, key, body[key])
        master_patch = {}
        if "bag_max_qty" in body:
            master_patch["bag_max_qty"] = _coerce_float(body.get("bag_max_qty"))
        if "bag_max_unit" in body:
            raw_unit = body.get("bag_max_unit")
            master_patch["bag_max_unit"] = raw_unit if not _is_blank_value(raw_unit) else None
        _upsert_menu_master(session, item, master_patch)
        logger.info("Menu item updated", week_id=week_id, item_id=item_id)
        record_event(
            "menu_edit",
            actor="system",
            target=item_id,
            wek=week_id,
            metadata={"fields": list(body.keys())},
        )
        return True


def _upsert_menu_master(session, item: MenuItem, master_patch: dict | None = None) -> None:
    name = (item.name or "").strip()
    if not name:
        return
    normalized = _normalize_menu_name(name)
    master = (
        session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
        .scalars()
        .first()
    )
    if not master:
        master = MenuMaster(id=f"MNU{uuid4().hex[:8]}", name=name, normalized_name=normalized)
        session.add(master)
    else:
        master.name = name

    facility_id = (item.facility_override or "").strip()
    if not facility_id:
        _apply_master_fields(master, item, master_patch)
    else:
        override = (
            session.execute(
                select(MenuFacilityOverride)
                .where(MenuFacilityOverride.menu_master_id == master.id)
                .where(MenuFacilityOverride.facility_id == facility_id)
            )
            .scalars()
            .first()
        )
        if not override:
            override = MenuFacilityOverride(
                id=f"MFO{uuid4().hex[:8]}",
                menu_master_id=master.id,
                facility_id=facility_id,
            )
            session.add(override)
        _apply_master_fields(override, item, master_patch)


def _apply_master_fields(target, item: MenuItem, master_patch: dict | None) -> None:
    for field in _MASTER_FIELDS:
        if master_patch is not None and field in master_patch:
            value = master_patch[field]
        else:
            value = getattr(item, field, None)
        if _is_blank_value(value):
            continue
        setattr(target, field, value)


def create_item_stub(week_id: str, name: str):
    with session_scope() as session:
        item_id = f"MEI{uuid4().hex[:6]}"
        item = MenuItem(id=item_id, weekly_menu_id=week_id, name=name)
        session.add(item)
        session.flush()
        session.refresh(item)
        return serialize_item(item)


def serialize_menu(menu: WeeklyMenu):
    return {"id": menu.id, "filename": menu.filename}


def serialize_item(item: MenuItem):
    return {
        "id": item.id,
        "week_id": item.weekly_menu_id,
        "name": item.name,
        "unit_type": item.unit_type,
        "qty_per_serving": item.qty_per_serving,
        "temp_type": item.temp_type,
        "daypart": item.daypart,
        "category": item.category,
        "facility_override": item.facility_override,
        "bag_max_qty": None,
        "bag_max_unit": None,
    }


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def get_menu_items_for_facility(week_id: str, facility_id: str | None) -> list[dict]:
    with session_scope() as session:
        items = session.query(MenuItem).filter(MenuItem.weekly_menu_id == week_id).all()
        if not items:
            return []

        if not facility_id:
            base_items = [serialize_item(i) for i in items if _is_blank(i.facility_override)]
            return _merge_master_defaults(base_items, None)

        base = {
            i.name: i
            for i in items
            if _is_blank(i.facility_override)
        }
        overrides = {
            i.name: i
            for i in items
            if (i.facility_override or "").strip() == facility_id
        }
        merged = {**base, **overrides}
        return _merge_master_defaults([serialize_item(i) for i in merged.values()], facility_id)


def get_menu(week_id: str) -> dict | None:
    with session_scope() as session:
        menu = session.get(WeeklyMenu, week_id)
        if not menu:
            return None
        items = session.query(MenuItem).filter(MenuItem.weekly_menu_id == week_id).all()
        payload = [serialize_item(i) for i in items]
        return {"menu": serialize_menu(menu), "items": _merge_master_defaults(payload, None)}


def get_menu_items(week_id: str) -> list[dict]:
    with session_scope() as session:
        items = session.query(MenuItem).filter(MenuItem.weekly_menu_id == week_id).all()
        return _merge_master_defaults([serialize_item(i) for i in items], None)


def get_item(item_id: str) -> dict | None:
    with session_scope() as session:
        item = session.get(MenuItem, item_id)
        if not item:
            return None
        merged = _merge_master_defaults([serialize_item(item)], item.facility_override)
        return merged[0] if merged else serialize_item(item)


def resolve_menu_defaults(names: list[str], facility_id: str | None = None) -> dict[str, dict]:
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    resolved: dict[str, dict] = {}
    for name in names:
        normalized = _normalize_menu_name(name or "")
        resolved[name] = defaults.get(normalized, {})
    return resolved
