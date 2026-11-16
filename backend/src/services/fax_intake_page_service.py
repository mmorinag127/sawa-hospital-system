"""Bind operator-selected original pages to one monthly order period."""
import base64
from hashlib import sha256
import re

import fitz

from src.services.hakodate_physical_menu_row_service import full_week_fax_period
from src.services.ingest_policy import require_explicit_ingest_week
from src.services.manual_upload_service import _split_pdf_pages, _validate_pdf_bytes
from src.services.sheet_week_service import parse_sheet_week_value


def period_context(facility_id, week_hint):
    if not isinstance(facility_id, str) or not facility_id.strip():
        raise ValueError('facility_hint_required')
    week = require_explicit_ingest_week(week_hint)
    month, start, end = parse_sheet_week_value(week)
    if start.strftime('%Y-%m') != month or end.strftime('%Y-%m') != month:
        raise ValueError('fax_target_period_must_be_monthly')
    first, last = full_week_fax_period(start, end)
    return {'facility_id': facility_id.strip(), 'week_hint': week,
            'date_from': start.isoformat(), 'date_to': end.isoformat(),
            'calendar_from': first.isoformat(), 'calendar_to': last.isoformat(),
            'cross_month': first.strftime('%Y-%m') != last.strftime('%Y-%m')}


def inspect_pages(pdf_bytes, filename, file_index, *, render=False):
    _validate_pdf_bytes(pdf_bytes)
    try:
        document = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception as exc:
        raise ValueError('fax_pdf_invalid') from exc
    with document:
        if document.is_encrypted or not 1 <= len(document) <= 40:
            raise ValueError('fax_page_count_or_encryption_unsupported')
        images = []
        if render:
            for page in document:
                scale = min(1.0, 720 / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                images.append('data:image/jpeg;base64,' + base64.b64encode(pixmap.tobytes('jpeg')).decode('ascii'))
    pages = _split_pdf_pages(pdf_bytes)
    return {'file_index': file_index, 'filename': filename, 'sha256': sha256(pdf_bytes).hexdigest(),
            'pages': [{'page_number': i, 'sha256': sha256(value).hexdigest(),
                       **({'image_url': images[i-1]} if render else {})}
                      for i, value in enumerate(pages, 1)]}


def selected_pages(pdf_bytes, filename, file_index, *, facility_id, week_hint, plan=None):
    context = period_context(facility_id, week_hint)
    actual = inspect_pages(pdf_bytes, filename, file_index)
    required = context['cross_month'] or len(actual['pages']) > 1
    if plan is None:
        if required:
            raise ValueError('fax_page_selection_required')
        return None
    if (not isinstance(plan, dict) or set(plan) != {'version', 'facility_id', 'week_hint', 'files'}
            or type(plan['version']) is not int or plan['version'] != 1
            or plan['facility_id'] != context['facility_id'] or plan['week_hint'] != context['week_hint']
            or not isinstance(plan['files'], list)):
        raise ValueError('fax_page_selection_context_changed')
    choices = [item for item in plan['files'] if isinstance(item, dict) and item.get('file_index') == file_index]
    if len(choices) != 1:
        raise ValueError('fax_page_selection_original_changed')
    chosen = choices[0]
    if (set(chosen) != {'file_index', 'sha256', 'pages'} or type(chosen['file_index']) is not int
            or chosen['sha256'] != actual['sha256'] or not isinstance(chosen['pages'], list)
            or len(chosen['pages']) != len(actual['pages'])):
        raise ValueError('fax_page_selection_original_changed')
    numbers = []
    for source, choice in zip(actual['pages'], chosen['pages'], strict=True):
        if (not isinstance(choice, dict) or set(choice) != {'page_number', 'sha256', 'include'}
                or type(choice['page_number']) is not int or type(choice['include']) is not bool
                or choice['page_number'] != source['page_number'] or choice['sha256'] != source['sha256']):
            raise ValueError('fax_page_selection_original_changed')
        if choice['include']:
            numbers.append(source['page_number'])
    return numbers


def period_source_scope(facility_id, week_hint):
    context = period_context(facility_id, week_hint)
    return sha256((context['facility_id'] + '\n' + context['week_hint']).encode()).hexdigest()[:16]


def protect_monthly_existing_order(payload, existing):
    if ':period:' not in payload.message_id:
        return
    match = re.fullmatch(r'upload:sha256:[0-9a-f]{24}:period:([0-9a-f]{16})', payload.message_id)
    if match is None or match[1] != period_source_scope(payload.facility_hint, payload.week_hint):
        raise ValueError('fax_page_selection_context_changed')
    if existing is not None and existing.message_id != payload.message_id:
        raise ValueError('fax_existing_order_requires_review')
