"""Interpret source documents by explicit headers, without applying instructions."""
from datetime import date
import re
import unicodedata
from src.services.school_lunch_cooking_parser import extract_tables


def text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def review_ocr_result(result):
    pages = result.get("pages") or []
    tables = []
    instructions = []
    reference_pages = []
    for page in pages:
        page_index = page["page_index"]
        markdown = page.get("markdown_text", "")
        candidates = page.get("tables", [])
        structured = [t for t in candidates if t.get("source") == "yomitoku_table_structure"]
        # Structured and markdown are two representations of the same page.
        selected = structured if structured else candidates
        has_allergy = False
        for table in selected:
            rows = table.get("rows", [])
            for index, row in enumerate(rows):
                headers = [re.sub(r"\s+", "", text(v)) for v in row]
                if not all(label in headers for label in ("対応献立名", "対応食材", "対応給食")):
                    continue
                has_allergy = True
                columns = {label: headers.index(label) for label in headers if label}
                normalized_page = text(markdown).replace("<br/>", "\n").replace("<br>", "\n")
                ym = re.search(r"令和\s*(\d+)年\s*(\d+)月", normalized_page)
                schools = [line.strip().lstrip("# ") for line in normalized_page.splitlines()
                           if re.fullmatch(r"[# ]*[^|<>\n]+(?:学校|学園)", line.strip())]
                schools = list(dict.fromkeys(schools))
                facility = schools[0] if len(schools) == 1 else ""
                for offset, values in enumerate(rows[index + 1:], index + 2):
                    def cell(label):
                        pos = columns.get(label)
                        return text(values[pos]) if pos is not None and pos < len(values) else ""
                    name = cell("対応献立名")
                    if not name:
                        continue
                    raw_day = cell("日")
                    service_date = ""
                    if ym and re.fullmatch(r"\d{1,2}", raw_day):
                        try:
                            service_date = date(int(ym[1]) + 2018, int(ym[2]), int(raw_day)).isoformat()
                        except ValueError:
                            pass
                    instructions.append({
                        "facility_name": facility, "service_date": service_date,
                        "raw_day": raw_day, "menu_name": name,
                        "target_food": cell("対応食材"), "instruction": cell("対応給食"),
                        "container": cell("容器"), "source_page": page_index,
                        "source": f"page{page_index}:{table.get('table_id', '')}:row{offset}",
                        "review_required": True,
                    })
                break
        if not has_allergy:
            tables.extend(selected)
            reference_pages.append(page_index)
    if instructions:
        parsed = {"document_type": "cooking_arrangement", "filename": result.get("filename", ""),
                  "provider": result.get("provider", "yomitoku"), "status": "needs_review",
                  "review_required": True, "menus": [], "issues": [{
                      "code": "allergy_instruction_review",
                      "message": "アレルギー対応表です。通常の材料表として保存せず、学校・日付・対象献立と除去／代替を確認してください。日付の結合セルも原本と照合してください。",
                  }]}
        parsed.update(source_kind="allergy_instructions", allergy_instructions=instructions,
                      reference_pages=reference_pages)
    else:
        parsed = extract_tables(tables, filename=result.get("filename", ""), provider=result.get("provider", "yomitoku"))
        parsed["source_kind"] = "cooking_arrangement" if parsed["menus"] else "reference_document"
        if not pages:
            parsed["issues"].append({"code": "source_pages_missing", "message": "元のOCRページがありません。原本から再読取が必要です。"})
    return {**parsed, "pages": pages}
