"""Populate the received 原本 package without a shape-dropping workbook round trip."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, time
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
X = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"s": S, "x": X, "a": A}
TEMPLATE = Path(__file__).parents[1] / "data" / "work_schedule_original.xlsx"
SOURCE_SHA256 = "4140d0a1e202b4b6267435881501560bf6a7d88b3f989a1d6bb1885ba3b88fd6"
SHEET = "xl/worksheets/sheet23.xml"
DRAWING = "xl/drawings/drawing22.xml"
LEGEND_IDS = {"2", "3", "4", "16", "17", "18"}
for prefix, uri in (("", S), ("xdr", X), ("a", A), ("r", R),
                    ("x14ac", "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac")):
    ET.register_namespace(prefix, uri)


def _xml(node):
    namespace = node.tag[1:].split("}")[0]
    if namespace in {P, "http://schemas.openxmlformats.org/package/2006/content-types"}:
        ET.register_namespace("", namespace)
    try:
        return ET.tostring(node, encoding="utf-8", xml_declaration=True)
    finally:
        ET.register_namespace("", S)


def _set(cell, value):
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, (float, int)):
        ET.SubElement(cell, f"{{{S}}}v").text = str(value)
    else:
        cell.set("t", "inlineStr")
        text = ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t")
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = str(value)


def extract_original_template(source: Path) -> bytes:
    """Build-time extraction: original sheet only, no daily sheets/unused strings."""
    import hashlib
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("受領原本のSHA256が一致しません")
    with ZipFile(source) as archive:
        names = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                 "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/theme/theme1.xml",
                 SHEET, "xl/worksheets/_rels/sheet23.xml.rels", DRAWING,
                 "xl/printerSettings/printerSettings23.bin"}
        parts = {name: archive.read(name) for name in names}
        strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    sheet = ET.fromstring(parts[SHEET])
    for cell in sheet.findall(".//s:c[@t='s']", NS):
        item = strings[int(cell.find(f"{{{S}}}v").text)]
        cell.remove(cell.find(f"{{{S}}}v"))
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{S}}}is")
        inline.extend(deepcopy(list(item)))
    sheet.attrib.pop("{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable", None)
    parts[SHEET] = _xml(sheet)
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    sheets = workbook.find("s:sheets", NS)
    for item in list(sheets):
        if item.get("name") != "原本":
            sheets.remove(item)
    workbook.find(".//s:workbookView", NS).set("activeTab", "0")
    parts["xl/workbook.xml"] = _xml(workbook)
    for name in ("xl/_rels/workbook.xml.rels", "_rels/.rels", "[Content_Types].xml"):
        root = ET.fromstring(parts[name])
        for item in list(root):
            target = item.get("PartName")
            if target is not None:
                retain = target.lstrip("/") in names
            elif item.get("Target"):
                base = "xl/" if name.startswith("xl/") else ""
                retain = base + item.get("Target") in names
            else:
                retain = True
            if not retain:
                root.remove(item)
        parts[name] = _xml(root)
    return _zip(parts)


def _zip(parts):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


def _minutes(value):
    parsed = time.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, time) or parsed.second or parsed.microsecond:
        raise ValueError("時間は分単位で指定してください")
    return parsed.hour * 60 + parsed.minute


def _anchor(parent, kind, col, row, offset=0):
    node = ET.SubElement(parent, f"{{{X}}}{kind}")
    for key, value in (("col", col), ("colOff", offset), ("row", row), ("rowOff", 0)):
        ET.SubElement(node, f"{{{X}}}{key}").text = str(value)
    return node


def _shape(identifier, row, start, end, text, color, column_emu, row_height=45):
    """A native editable text box or exact duration band, never a raster chart."""
    anchor = ET.Element(f"{{{X}}}twoCellAnchor", editAs="twoCell")
    for kind, position in (("from", start), ("to", end)):
        col = int(position)
        point = _anchor(anchor, kind, col, row, round((position-col)*column_emu))
        bottom = round(row_height * 12700)
        point.find(f"{{{X}}}rowOff").text = str(40000 if text else 0)
        if kind == "to":
            point.find(f"{{{X}}}rowOff").text = str(bottom - 40000 if text else bottom)
    shape = ET.SubElement(anchor, f"{{{X}}}sp")
    nv = ET.SubElement(shape, f"{{{X}}}nvSpPr")
    ET.SubElement(nv, f"{{{X}}}cNvPr", id=str(identifier), name=f"工程{'内容' if text else '時間'} {identifier}")
    ET.SubElement(nv, f"{{{X}}}cNvSpPr")
    props = ET.SubElement(shape, f"{{{X}}}spPr")
    ET.SubElement(ET.SubElement(props, f"{{{A}}}prstGeom", prst="rect"), f"{{{A}}}avLst")
    fill = ET.SubElement(props, f"{{{A}}}{'noFill' if text else 'solidFill'}")
    if not text:
        ET.SubElement(fill, f"{{{A}}}srgbClr", val=color)
    ET.SubElement(ET.SubElement(props, f"{{{A}}}ln"), f"{{{A}}}noFill")
    if text:
        body = ET.SubElement(shape, f"{{{X}}}txBody")
        ET.SubElement(body, f"{{{A}}}bodyPr", wrap="square", lIns="0", rIns="0", tIns="0", bIns="0")
        ET.SubElement(body, f"{{{A}}}lstStyle")
        for line in text.splitlines():
            para = ET.SubElement(body, f"{{{A}}}p")
            run = ET.SubElement(para, f"{{{A}}}r")
            font = ET.SubElement(run, f"{{{A}}}rPr", lang="ja-JP", sz="1000")
            ET.SubElement(font, f"{{{A}}}ea", typeface="ＭＳ Ｐゴシック")
            ET.SubElement(run, f"{{{A}}}t").text = line
    ET.SubElement(anchor, f"{{{X}}}clientData")
    return anchor


def generate_original_work_schedule(schedule: dict) -> bytes:
    from src.services.school_lunch_hygiene import inspect_hygiene, ownership_rows
    from src.services.work_schedule_service import _work_schedule_lanes, _wrap_schedule_text

    ticks = schedule.get("timeline_ticks")
    if not ticks or len(ticks) != 8:
        raise ValueError("原本C10:J10に使用する8個の時間目盛を明示設定してください")
    minutes = [_minutes(tick) for tick in ticks]
    if any(a >= b for a, b in zip(minutes, minutes[1:])):
        raise ValueError("時間目盛は同日内で昇順にしてください")
    rows = schedule.get("rows")
    if not rows or any(not row.get("tasks") for row in rows):
        raise ValueError("出力する献立と作業を入力してください")
    rows = ownership_rows(rows)
    hygiene = inspect_hygiene({**schedule, "rows": rows}, require_complete=True)
    for row in rows:
        for task in row["tasks"]:
            start, end = _minutes(task["start_time"]), _minutes(task["end_time"])
            if not minutes[0] <= start < end <= minutes[-1]:
                raise ValueError("作業時刻が時間目盛の範囲外です。目盛または作業時刻を確認してください")
            if task["work_zone"] not in {"contaminated", "contaminated_meat_fish", "non_contaminated"}:
                raise ValueError("作業の汚染区分を設定してください")
    with ZipFile(TEMPLATE) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    sheet, drawing = ET.fromstring(parts[SHEET]), ET.fromstring(parts[DRAWING])
    cells = {cell.get("r"): cell for cell in sheet.findall(".//s:c", NS)}
    day = schedule["service_date"]
    if isinstance(day, str):
        day = date.fromisoformat(day)
    _set(cells["A1"], f"{day.year}年 {day.month}月 {day.day}日（{'月火水木金土日'[day.weekday()]}）")
    _set(cells["C1"], schedule["facility"]["name"])
    _set(cells["G1"], "記入者" + "　" * 10 + "印")  # no operator author was supplied
    # C:D is the source's school-name space; E starts the document title.
    # Bound the full value using native cell sizing, without changing the
    # original row height, columns, font definition or neighboring styles.
    styles = ET.fromstring(parts["xl/styles.xml"])
    formats = styles.find("s:cellXfs", NS)
    school_style = deepcopy(formats[int(cells["C1"].get("s"))])
    alignment = school_style.find("s:alignment", NS)
    if alignment is None:
        alignment = ET.SubElement(school_style, f"{{{S}}}alignment")
    alignment.set("shrinkToFit", "1")
    alignment.set("wrapText", "0")
    school_style.set("applyAlignment", "1")
    cells["C1"].set("s", str(len(formats)))
    formats.append(school_style)
    formats.set("count", str(len(formats)))
    parts["xl/styles.xml"] = _xml(styles)
    _set(cells["B6"], f"{_minutes(schedule['plating_start'])//60:02d}:{_minutes(schedule['plating_start'])%60:02d}～{_minutes(schedule['plating_end'])//60:02d}:{_minutes(schedule['plating_end'])%60:02d}")
    for column, value in zip("CDEFGHIJ", minutes):
        _set(cells[f"{column}10"], value / 1440)
    # Keep only common legend shapes. All examples (rice, hygiene, arrows) are daily data.
    for anchor in list(drawing):
        if anchor.find(".//x:cNvPr", NS).get("id") not in LEGEND_IDS:
            drawing.remove(anchor)
    def position(value):
        if value == minutes[-1]:
            return 1.0
        index = next(i for i in range(7) if minutes[i] <= value < minutes[i+1])
        return (index + (value-minutes[index])/(minutes[index+1]-minutes[index])) / 7

    original_shapes = {a.find(".//x:cNvPr", NS).get("id"): a for a in drawing}
    def left_of_column(identifier):
        shape = original_shapes[identifier]
        return int(shape.find(".//a:xfrm/a:off", NS).get("x")) - int(shape.findtext("x:from/x:colOff", namespaces=NS))
    column_emu = (left_of_column("2") - left_of_column("3")) // 2
    plans = []
    for row_index, row in enumerate(rows):
        extra = {}
        for task_index in range(len(row["tasks"])):
            events = [event for event in hygiene["events"] if event["row"] == row_index and event["task"] == task_index]
            if events:
                extra[task_index] = " / ".join(dict.fromkeys(
                    f"{_minutes(event['time'])//60:02d}:{_minutes(event['time'])%60:02d} {event['label']}"
                    for event in events))
        plans.append(_work_schedule_lanes(row["tasks"], position, 7 * column_emu / 12700, extra))
    for note in schedule.get("source_notes", []):
        text = f"原本から採用した注記｜{note['source']}\n{note['text']}"
        lines = _wrap_schedule_text(text, 7 * column_emu / 12700 / 5.25, 10)
        rows.append({"menu_name": "原本注記", "assignees": [], "tasks": []})
        plans.append([{"height": len(lines[offset:offset+20]) * 14 + 16, "entries": [{
            "task": {}, "duration": False, "text_start": 0, "text_end": 1,
            "text": "\n".join(lines[offset:offset+20]),
        }]} for offset in range(0, len(lines), 20)])
    # The template prints A4 landscape at one-page width. Plan page breaks
    # before merging ownership cells, so neither a menu nor its assignees is
    # silently split by the spreadsheet renderer's automatic pagination.
    margins = sheet.find("s:pageMargins", NS)
    page_width, page_height = 841.8898, 595.2756
    usable_width = page_width - 72 * (float(margins.get("left")) + float(margins.get("right")))
    usable_height = page_height - 72 * (float(margins.get("top")) + float(margins.get("bottom")))
    reference_col = int(original_shapes["3"].findtext("x:from/x:col", namespaces=NS))
    sheet_width = (left_of_column("3") + (10-reference_col) * column_emu) / 12700
    scale = min(1, usable_width / sheet_width)
    header_height = sum(float(row.get("ht", "15")) for row in sheet.findall("s:sheetData/s:row", NS) if int(row.get("r")) <= 10)
    body_height = usable_height / scale - header_height - 8  # printer rounding reserve, not text shrinking
    ownership, page_starts = [], [11]
    number, used = 11, 0
    for row, plan in zip(rows, plans):
        total = sum(lane["height"] for lane in plan)
        if used and total <= body_height and used + total > body_height:
            page_starts.append(number)
            used = 0
        begin = number
        continued = False
        for lane in plan:
            if lane["height"] > body_height:
                raise ValueError("1行の内容が印刷可能な高さを超えています。工程を分割してください")
            if used and used + lane["height"] > body_height:
                if number > begin:
                    ownership.append((begin, number-1, row, continued))
                    continued = True
                page_starts.append(number)
                begin, used = number, 0
            used += lane["height"]
            number += 1
        ownership.append((begin, number-1, row, continued))
    data = sheet.find("s:sheetData", NS)
    template_row = deepcopy(data.find("s:row[@r='12']", NS))
    for row in list(data):
        if int(row.get("r")) >= 11:
            data.remove(row)
    last_row = max(11, 10 + sum(len(plan) for plan in plans))
    for number in range(11, last_row + 1):
        row = deepcopy(template_row)
        row.set("r", str(number))
        for cell in row:
            column = ''.join(c for c in cell.get("r") if c.isalpha())
            cell.set("r", f"{column}{number}")
            cell.set("s", "44")  # original unfilled grid style; no example zone band
            _set(cell, None)
            cells[cell.get("r")] = cell
        data.append(row)
    delivery = schedule.get("delivery_assignees") or []
    merges = sheet.find("s:mergeCells", NS)
    ET.SubElement(merges, f"{{{S}}}mergeCell", ref="C1:D1")
    for merge in list(merges):
        if merge.get("ref").startswith("J11:"):
            merges.remove(merge)
    for begin, end in zip(page_starts, [*page_starts[1:], last_row+1]):
        _set(cells[f"J{begin}"], "配送\n" + "・".join(delivery) if delivery else None)
        if end-begin > 1:
            ET.SubElement(merges, f"{{{S}}}mergeCell", ref=f"J{begin}:J{end-1}")
    number, identifier = 11, 100
    global_events = [event for event in hygiene["events"] if event["row"] is None]
    if global_events:
        label = "\n".join(f"{_minutes(event['time'])//60:02d}:{_minutes(event['time'])%60:02d} {event['label']}" for event in global_events)
        drawing.append(_shape(identifier, 7, 2.05, 8.95, label, None, column_emu))
        identifier += 1
    for row, plan in zip(rows, plans):
        for lane in plan:
            row_height = lane["height"]
            data.find(f"s:row[@r='{number}']", NS).set("ht", str(row_height))
            for entry in lane["entries"]:
                color = {"contaminated": "FAC090", "contaminated_meat_fish": "FFFF99", "non_contaminated": "CCFFFF"}.get(entry["task"].get("work_zone"))
                if entry["duration"]:
                    drawing.append(_shape(identifier, number-1, 2.5 + 7 * entry["start"],
                                          2.5 + 7 * entry["end"], None, color, column_emu, row_height))
                    identifier += 1
                drawing.append(_shape(identifier, number-1, 2.5 + 7 * entry["text_start"],
                                      2.5 + 7 * entry["text_end"], entry["text"], color, column_emu, row_height))
                identifier += 1
            number += 1
    for begin, end, row, continued in ownership:
        _set(cells[f"A{begin}"], row["menu_name"] + ("（続き）" if continued else ""))
        _set(cells[f"B{begin}"], "\n".join(row["assignees"]))
        if end > begin:
            for col in "AB":
                ET.SubElement(merges, f"{{{S}}}mergeCell", ref=f"{col}{begin}:{col}{end}")
    merges.set("count", str(len(merges)))
    sheet.find("s:dimension", NS).set("ref", f"A1:J{last_row}")
    page = sheet.find("s:pageSetup", NS)
    page.attrib.pop("scale", None)
    page.set("fitToWidth", "1")
    page.set("fitToHeight", "0")
    if len(page_starts) > 1:
        breaks = ET.Element(f"{{{S}}}rowBreaks", count=str(len(page_starts)-1), manualBreakCount=str(len(page_starts)-1))
        for number in page_starts[1:]:
            ET.SubElement(breaks, f"{{{S}}}brk", id=str(number-1), min="0", max="16383", man="1")
        sheet.insert(list(sheet).index(sheet.find("s:drawing", NS)), breaks)
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    name = str(day.day)
    workbook.find("s:sheets/s:sheet", NS).set("name", name)
    definitions = ET.Element(f"{{{S}}}definedNames")
    ET.SubElement(definitions, f"{{{S}}}definedName", name="_xlnm.Print_Area", localSheetId="0").text = f"'{name}'!$A$1:$J${last_row}"
    ET.SubElement(definitions, f"{{{S}}}definedName", name="_xlnm.Print_Titles", localSheetId="0").text = f"'{name}'!$1:$10"
    workbook.insert(list(workbook).index(workbook.find("s:calcPr", NS)), definitions)
    parts[SHEET], parts[DRAWING], parts["xl/workbook.xml"] = _xml(sheet), _xml(drawing), _xml(workbook)
    return _zip(parts)


def generate_monthly_original_work_schedule(schedules: list[dict]) -> bytes:
    """Join native day sheets without an openpyxl shape-dropping round trip."""
    days = []
    for schedule in schedules:
        with ZipFile(BytesIO(generate_original_work_schedule(schedule))) as archive:
            days.append({name: archive.read(name) for name in archive.namelist()})
    if not days:
        raise ValueError("出力する工程表がありません")
    parts = dict(days[0])
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    sheets = workbook.find("s:sheets", NS)
    sheets.clear()
    names = workbook.find("s:definedNames", NS)
    names.clear()
    relationships = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    types = ET.fromstring(parts["[Content_Types].xml"])
    for relation in list(relationships):
        if relation.get("Type", "").endswith("/worksheet"):
            relationships.remove(relation)
    for item in list(types):
        if item.get("PartName", "").endswith(("/sheet23.xml", "/drawing22.xml")):
            types.remove(item)
    for name in (SHEET, DRAWING, "xl/worksheets/_rels/sheet23.xml.rels"):
        parts.pop(name)
    for index, day in enumerate(days, start=1):
        sheet_path, drawing_path = f"xl/worksheets/day{index}.xml", f"xl/drawings/day{index}.xml"
        sheet_rels = ET.fromstring(day["xl/worksheets/_rels/sheet23.xml.rels"])
        for relation in sheet_rels:
            if relation.get("Type", "").endswith("/drawing"):
                relation.set("Target", f"../drawings/day{index}.xml")
        parts[sheet_path], parts[drawing_path] = day[SHEET], day[DRAWING]
        parts[f"xl/worksheets/_rels/day{index}.xml.rels"] = _xml(sheet_rels)
        daily_workbook = ET.fromstring(day["xl/workbook.xml"])
        daily_sheet = deepcopy(daily_workbook.find("s:sheets/s:sheet", NS))
        daily_sheet.set("sheetId", str(index))
        daily_sheet.set(f"{{{R}}}id", f"day{index}")
        sheets.append(daily_sheet)
        for item in daily_workbook.find("s:definedNames", NS):
            item.set("localSheetId", str(index-1))
            names.append(deepcopy(item))
        ET.SubElement(relationships, f"{{{P}}}Relationship", Id=f"day{index}", Type=R + "/worksheet", Target=f"worksheets/day{index}.xml")
        for path, kind in ((sheet_path, "spreadsheetml.worksheet"), (drawing_path, "drawing")):
            ET.SubElement(types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                          PartName="/" + path, ContentType=f"application/vnd.openxmlformats-officedocument.{kind}+xml")
    parts["xl/workbook.xml"] = _xml(workbook)
    parts["xl/_rels/workbook.xml.rels"] = _xml(relationships)
    parts["[Content_Types].xml"] = _xml(types)
    return _zip(parts)
