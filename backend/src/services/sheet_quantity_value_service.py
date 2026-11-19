"""Keep editable quantities separate from uncertain OCR text without backfilling DB rows."""
from copy import deepcopy
import math
import re

KEY = 'quantity_ocr_review'
_NUMBER = re.compile(r'-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?')
_DIGITS = str.maketrans('０１２３４５６７８９．－，', '0123456789.-,')


def numeric_value(value):
    text = str(value if value is not None else '').strip().translate(_DIGITS)
    if not _NUMBER.fullmatch(text):
        return None
    number = float(text.replace(',', ''))
    return number if math.isfinite(number) else None


def is_quantity_field(field):
    return str(field).startswith(('qty.', 'qty_', 'quantity'))


def editable_sheet(sheet):
    if not isinstance(sheet, dict):
        return sheet
    result = deepcopy(sheet)
    fields, rows = result.get('fields'), result.get('rows')
    if not isinstance(fields, list) or not isinstance(rows, list):
        return result
    reviews = {}
    review_items = result.get(KEY) or []
    if not isinstance(review_items, list):
        raise ValueError('quantity_review_identity_invalid')
    for item in review_items:
        if (not isinstance(item, dict) or type(item.get('row_index')) is not int
                or type(item.get('col_index')) is not int):
            raise ValueError('quantity_review_identity_invalid')
        r, c = item['row_index'], item['col_index']
        if (not 0 <= r < len(rows) or not isinstance(rows[r], list)
                or not 0 <= c < len(fields) or c >= len(rows[r])
                or fields[c] != item.get('field') or not is_quantity_field(fields[c])
                or not isinstance(item.get('raw_text'), str)
                or type(item.get('confirmed_blank', False)) is not bool or (r,c) in reviews):
            raise ValueError('quantity_review_identity_invalid')
        reviews[r,c] = item
    projection = result.get('hakodate_evidence_projection') or {}
    for cell in projection.get('applied', []):
        r, field = cell.get('row_index'), cell.get('field')
        if type(r) is not int or field not in fields or not is_quantity_field(field):
            continue
        c = fields.index(field)
        raw = str(cell.get('value') if cell.get('value') is not None else '')
        if (0 <= r < len(rows) and isinstance(rows[r], list) and c < len(rows[r])
                and not str(rows[r][c] or '').strip() and raw.strip() and numeric_value(raw) is None
                and (r,c) not in reviews):
            reviews[r,c] = {'row_index':r,'col_index':c,'field':field,'raw_text':raw,'confirmed_blank':False}
    for r, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        for c, field in enumerate(fields[:len(row)]):
            if not is_quantity_field(field):
                continue
            raw = str(row[c] if row[c] is not None else '')
            if raw.strip() and numeric_value(raw) is None:
                reviews[r,c] = {'row_index':r, 'col_index':c, 'field':field,
                    'raw_text':raw, 'confirmed_blank':False}
                row[c] = ''
    if reviews:
        result[KEY] = list(reviews.values())
    changed = 0
    for item in result.get('ocr_numeric_cell_items') or []:
        if not isinstance(item, dict):
            continue
        c = item.get('target_col_index')
        value = str(item.get('value') or '')
        if (type(c) is int and 0 <= c < len(fields) and is_quantity_field(fields[c])
                and value.strip() and numeric_value(value) is None):
            changed += int(item.get('classification') == 'accepted')
            item.update(raw_text=value, value='', classification='unresolved', reason='quantity_not_numeric')
    if changed and isinstance(result.get('ocr_numeric_cell_summary'), dict):
        summary = result['ocr_numeric_cell_summary']
        summary['accepted_count'] = max(0, int(summary.get('accepted_count', 0))-changed)
        summary['unresolved_count'] = int(summary.get('unresolved_count', 0))+changed
    return result


def pending_cells(sheet):
    clean = editable_sheet(sheet)
    if not isinstance(clean, dict):
        return []
    return [item for item in clean.get(KEY, [])
        if not str(clean['rows'][item['row_index']][item['col_index']] or '').strip()
        and not item.get('confirmed_blank', False)]
