import argparse
import re
import zipfile
import xml.etree.ElementTree as ET
from uuid import uuid4

from sqlalchemy import select

from src.db import Base, engine, session_scope
from src.models.menu import MenuMaster, MenuFacilityOverride

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

FULLWIDTH_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)

HEADER_TOKENS = (
    "メニュー名",
    "副菜",
    "主菜",
    "献立",
    "副①",
    "副②",
    "主Ａ",
    "主A",
    "朝アサ",
    "昼ヒル",
    "夕ユウ",
)

GROUP_TOKENS = (
    "カレー",
    "丼",
    "個数物",
)

NOTE_TOKENS = (
    "献立例",
    "提供",
    "場合",
    "その場合",
)


def _normalize_name(value: str) -> str:
    if not value:
        return ""
    text = value.translate(FULLWIDTH_TRANSLATION)
    text = re.sub(r"\s+", "", text)
    text = text.replace("・", "").replace("／", "/")
    return text.strip().lower()


def _is_header(value: str) -> bool:
    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return True
    if stripped.startswith("(") or stripped.startswith("（"):
        return True
    return any(token in stripped for token in HEADER_TOKENS)


def _is_note(value: str) -> bool:
    if not value:
        return False
    stripped = value.strip()
    if any(token in stripped for token in NOTE_TOKENS):
        return True
    if "。" in stripped and len(stripped) > 20:
        return True
    return False


def _parse_qty(value: str):
    if not value:
        return None, None
    text = value.translate(FULLWIDTH_TRANSLATION).replace("ｇ", "g").replace("Ｇ", "g")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None, None
    qty = float(match.group(1))
    unit = None
    if "g" in text.lower():
        unit = "g"
    elif "個" in value or "コ" in value:
        unit = "count"
    elif "切" in value or "キ" in value:
        unit = "cut"
    return qty, unit


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    strings = []
    for si in root.findall("a:si", NS):
        texts = []
        for t in si.findall(".//a:t", NS):
            texts.append(t.text or "")
        strings.append("".join(texts))
    return strings


def _sheet_mapping(zf: zipfile.ZipFile) -> dict[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    mapping = {}
    for sheet in wb.findall("a:sheets/a:sheet", NS):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = rel_map.get(rel_id)
        if target and not target.startswith("xl/"):
            target = f"xl/{target}"
        mapping[name] = target
    return mapping


def _load_rows(sheet_xml: bytes, shared: list[str], max_cols: int = 25) -> dict[int, dict[int, str]]:
    root = ET.fromstring(sheet_xml)
    rows: dict[int, dict[int, str]] = {}
    for cell in root.findall(".//a:sheetData/a:row/a:c", NS):
        cell_ref = cell.attrib.get("r")
        if not cell_ref:
            continue
        row_num = int("".join(ch for ch in cell_ref if ch.isdigit()))
        col_letters = "".join(ch for ch in cell_ref if ch.isalpha())
        col_idx = 0
        for ch in col_letters:
            col_idx = col_idx * 26 + (ord(ch.upper()) - 64)
        if col_idx > max_cols:
            continue
        v = cell.find("a:v", NS)
        if v is None:
            value = ""
        else:
            if cell.attrib.get("t") == "s":
                idx = int(v.text)
                value = shared[idx] if idx < len(shared) else ""
            else:
                value = v.text or ""
        rows.setdefault(row_num, {})[col_idx] = value
    return rows


def _build_items(rows: dict[int, dict[int, str]]):
    items = []
    overrides = []
    curry_mode = False
    count_mode_side = False
    count_mode_main = False

    for row_num in sorted(rows):
        row = rows[row_num]
        col1 = row.get(1, "")
        col2 = row.get(2, "")
        col3 = row.get(3, "")
        col5 = row.get(5, "")
        col7 = row.get(7, "")
        col8 = row.get(8, "")
        col10 = row.get(10, "")
        col11 = row.get(11, "")
        col13 = row.get(13, "")
        col14 = row.get(14, "")
        col17 = row.get(17, "")
        col19 = row.get(19, "")
        col20 = row.get(20, "")

        if col3 in ("朝アサ", "昼ヒル", "夕ユウ") or (col2 and "献立例" in col2):
            curry_mode = False
        if col8 and any(token in col8 for token in GROUP_TOKENS if token in ("カレー", "丼")):
            curry_mode = True
        if col1 and "個数物" in col1:
            count_mode_side = True
        if col14 and "個数物" in col14:
            count_mode_main = True

        if col2 and not _is_header(col2) and not _is_note(col2) and "個数物" not in col2:
            bag_max_qty, bag_max_unit = _parse_qty(col5)
            qty_per, unit = _parse_qty(col7)
            if count_mode_side and (unit is None):
                unit = "count"
            items.append(
                {
                    "name": col2,
                    "unit_type": unit,
                    "qty_per_serving": qty_per,
                    "bag_max_qty": bag_max_qty,
                    "bag_max_unit": bag_max_unit,
                    "temp_type": None if count_mode_side else "温菜",
                    "category": "個数物" if count_mode_side else "副菜（温菜）",
                }
            )

        if col8 and not _is_header(col8) and not _is_note(col8) and "個数物" not in col8:
            if curry_mode:
                qty_per, unit = _parse_qty(col10)
                items.append(
                    {
                        "name": col8,
                        "unit_type": unit or "g",
                        "qty_per_serving": qty_per,
                        "bag_max_qty": None,
                        "bag_max_unit": None,
                        "temp_type": "温菜",
                        "category": "カレー・丼もの",
                    }
                )
            else:
                bag_max_qty, bag_max_unit = _parse_qty(col11)
                qty_per, unit = _parse_qty(col13)
                items.append(
                    {
                        "name": col8,
                        "unit_type": unit,
                        "qty_per_serving": qty_per,
                        "bag_max_qty": bag_max_qty,
                        "bag_max_unit": bag_max_unit,
                        "temp_type": "冷菜",
                        "category": "副菜（冷菜）",
                    }
                )

        if col14 and not _is_header(col14) and not _is_note(col14) and "個数物" not in col14:
            bag_max_qty, bag_max_unit = _parse_qty(col17)
            qty_per, unit = _parse_qty(col19)
            items.append(
                {
                    "name": col14,
                    "unit_type": unit,
                    "qty_per_serving": qty_per,
                    "bag_max_qty": bag_max_qty,
                    "bag_max_unit": bag_max_unit,
                    "temp_type": "温菜",
                    "category": "個数物" if count_mode_main else "主菜（温菜）",
                }
            )
            group_qty, group_unit = _parse_qty(col20)
            if group_qty is not None:
                overrides.append(
                    {
                        "name": col14,
                        "unit_type": group_unit or unit,
                        "qty_per_serving": group_qty,
                    }
                )

    return items, overrides


def _upsert_menu_master(session, item: dict, cache: dict[str, MenuMaster]) -> MenuMaster:
    normalized = _normalize_name(item["name"])
    master = cache.get(normalized)
    if master is None:
        master = (
            session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
            .scalars()
            .first()
        )
        if master:
            cache[normalized] = master
    if not master:
        master = MenuMaster(
            id=f"MNU{uuid4().hex[:8]}",
            name=item["name"],
            normalized_name=normalized,
        )
        session.add(master)
        cache[normalized] = master
    else:
        master.name = item["name"]
    for field in ("unit_type", "qty_per_serving", "bag_max_qty", "bag_max_unit", "temp_type", "category"):
        value = item.get(field)
        if value is not None:
            setattr(master, field, value)
    return master


def _upsert_override(session, master_id: str, facility_id: str, payload: dict):
    override = (
        session.execute(
            select(MenuFacilityOverride)
            .where(MenuFacilityOverride.menu_master_id == master_id)
            .where(MenuFacilityOverride.facility_id == facility_id)
        )
        .scalars()
        .first()
    )
    if not override:
        override = MenuFacilityOverride(
            id=f"MFO{uuid4().hex[:8]}",
            menu_master_id=master_id,
            facility_id=facility_id,
        )
        session.add(override)
    for field in ("unit_type", "qty_per_serving"):
        value = payload.get(field)
        if value is not None:
            setattr(override, field, value)


def seed_menu_master(path: str, override_facilities: list[str]):
    Base.metadata.create_all(bind=engine)
    with zipfile.ZipFile(path) as zf:
        shared = _load_shared_strings(zf)
        mapping = _sheet_mapping(zf)
        sheet_path = mapping.get("Sheet1")
        if not sheet_path:
            raise RuntimeError("Sheet1 not found")
        rows = _load_rows(zf.read(sheet_path), shared)
        items, overrides = _build_items(rows)

    with session_scope() as session:
        cache: dict[str, MenuMaster] = {}
        for item in items:
            if not item.get("name"):
                continue
            master = _upsert_menu_master(session, item, cache)
            for override in overrides:
                if override["name"] != item["name"]:
                    continue
                for facility_id in override_facilities:
                    _upsert_override(session, master.id, facility_id, override)

    return len(items), len(overrides)


def main():
    parser = argparse.ArgumentParser(description="Seed menu master from menu rule sheet.")
    parser.add_argument(
        "--path",
        default="input_example/献立名　決まり.xlsx",
        help="Path to rule spreadsheet.",
    )
    parser.add_argument(
        "--override-facilities",
        default="FAC00001,FAC00006",
        help="Comma-separated facility IDs for 池袋・ふれあい group overrides.",
    )
    args = parser.parse_args()

    facilities = [item.strip() for item in args.override_facilities.split(",") if item.strip()]
    count_items, count_overrides = seed_menu_master(args.path, facilities)
    print(f"Seeded {count_items} menu rows, overrides {count_overrides}.")


if __name__ == "__main__":
    main()
