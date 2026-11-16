"""Authored page routing, not image recognition or a live FAX oracle."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import fitz
import pytest

from src.services import fax_intake_page_service as pages, manual_upload_service as storage

NOV = '2026-11@2026-11-29~2026-11-30'
DEC = '2026-12@2026-12-01~2026-12-05'


def original(count=2):
    with fitz.open() as pdf:
        for index in range(count):
            pdf.new_page().insert_text((30, 30), f'Authored FAX source page {index+1}')
        return pdf.tobytes()


def plan(data, week=NOV, include=(1,)):
    source = pages.inspect_pages(data, 'original.pdf', 0)
    return {'version': 1, 'facility_id': 'F', 'week_hint': week,
        'files': [{k: v for k, v in source.items() if k != 'filename'}]}


def selected_plan(data, week=NOV, include=(1,)):
    result = plan(data, week)
    for page in result['files'][0]['pages']:
        page['include'] = page['page_number'] in include
    return result


@pytest.mark.parametrize('week,first,last', [
    (NOV, '2026-11-29', '2026-12-05'), (DEC, '2026-11-29', '2026-12-05'),
    ('2026-12@2026-12-27~2026-12-31', '2026-12-27', '2027-01-02'),
    ('2027-01@2027-01-01~2027-01-02', '2026-12-27', '2027-01-02')])
def test_full_physical_week_and_monthly_target_are_separate(week, first, last):
    context = pages.period_context('F', week)
    assert context['calendar_from'] == first and context['calendar_to'] == last
    assert context['cross_month'] is True


def test_monthly_page_choices_preserve_source_and_stable_split_bytes(monkeypatch, tmp_path):
    data = original()
    assert storage._split_pdf_pages(data) == storage._split_pdf_pages(data)
    assert pages.selected_pages(data, 'original.pdf', 0, facility_id='F', week_hint=NOV,
        plan=selected_plan(data)) == [1]
    assert pages.selected_pages(data, 'original.pdf', 0, facility_id='F', week_hint=DEC,
        plan=selected_plan(data, DEC, (2,))) == [2]
    monkeypatch.setattr(storage, '_resolve_storage_target', lambda when:
        storage._ManualUploadStorageTarget(mode='local_ephemeral', prefix='test',
            storage=storage.StorageService(tmp_path)))
    november = storage.save_uploaded_pdf_pages(pdf_bytes=data, original_filename='original.pdf',
        received_at=datetime(2026,11,16), page_numbers=[1], source_scope=pages.period_source_scope('F',NOV))
    december = storage.save_uploaded_pdf_pages(pdf_bytes=data, original_filename='original.pdf',
        received_at=datetime(2026,11,16), page_numbers=[2], source_scope=pages.period_source_scope('F',DEC))
    assert [r.page_number for r in november] == [1] and [r.page_number for r in december] == [2]
    assert all(r.total_pages == 2 for r in november+december)
    for expected, row in zip((1,2), november+december):
        with fitz.open(row.pdf_uri) as saved:
            assert len(saved) == 1 and f'page {expected}' in saved[0].get_text()


def test_same_seven_day_page_can_be_registered_for_each_month_without_id_collision(monkeypatch, tmp_path):
    data = original(1)
    monkeypatch.setattr(storage, '_resolve_storage_target', lambda when:
        storage._ManualUploadStorageTarget(mode='local_ephemeral', prefix='test', storage=storage.StorageService(tmp_path)))
    def save(week):
        return storage.save_uploaded_pdf_pages(pdf_bytes=data, original_filename='same.pdf',
            received_at=datetime(2026,11,16), page_numbers=[1], source_scope=pages.period_source_scope('F',week))[0]
    first, other, repeat = save(NOV), save(DEC), save(NOV)
    assert first.message_id == repeat.message_id != other.message_id
    assert first.content_sha256 == other.content_sha256
    assert first.pdf_uri == other.pdf_uri


@pytest.mark.parametrize('change', ['facility', 'week', 'parent', 'page', 'page_number', 'extra', 'duplicate_index'])
def test_stale_or_tampered_page_confirmation_is_rejected(change):
    data = original()
    capture = selected_plan(data)
    if change == 'facility': capture['facility_id'] = 'G'
    if change == 'week': capture['week_hint'] = DEC
    if change == 'parent': capture['files'][0]['sha256'] = '0'*64
    if change == 'page': capture['files'][0]['pages'][0]['sha256'] = '0'*64
    if change == 'page_number': capture['files'][0]['pages'][0]['page_number'] = True
    if change == 'extra': capture['files'][0]['pages'][0]['inferred_month'] = '2026-11'
    if change == 'duplicate_index': capture['files'].append(deepcopy(capture['files'][0]))
    with pytest.raises(ValueError, match='fax_page_selection_'):
        pages.selected_pages(data, 'original.pdf', 0, facility_id='F', week_hint=NOV, plan=capture)


@pytest.mark.parametrize('week,count', [(NOV,1), (NOV,2), ('2026-11@2026-11-15~2026-11-21',2)])
def test_cross_month_or_multiple_pages_never_inherit_unconfirmed_week(week,count):
    with pytest.raises(ValueError, match='fax_page_selection_required'):
        pages.selected_pages(original(count), 'original.pdf', 0, facility_id='F', week_hint=week)


def test_explicitly_excluded_page_is_not_registered():
    data = original()
    assert pages.selected_pages(data, 'original.pdf', 0, facility_id='F', week_hint=NOV,
        plan=selected_plan(data, include=())) == []


def test_preview_is_read_only_and_shows_every_page():
    data = original()
    preview = pages.inspect_pages(data, 'original.pdf', 0, render=True)
    assert [r['page_number'] for r in preview['pages']] == [1,2]
    assert all(p['image_url'].startswith('data:image/jpeg;base64,') for p in preview['pages'])


def test_existing_order_cannot_be_reassigned_by_monthly_intake():
    message = 'upload:sha256:'+'a'*24+':period:'+pages.period_source_scope('F',NOV)
    payload = SimpleNamespace(message_id=message, facility_hint='F', week_hint=NOV)
    existing = SimpleNamespace(message_id='old-input', facility_code='F', week_code=NOV)
    before = vars(existing).copy()
    with pytest.raises(ValueError, match='fax_existing_order_requires_review'):
        pages.protect_monthly_existing_order(payload, existing)
    assert vars(existing) == before
    existing.message_id = message
    pages.protect_monthly_existing_order(payload, existing)
    payload.week_hint = DEC
    with pytest.raises(ValueError, match='fax_page_selection_context_changed'):
        pages.protect_monthly_existing_order(payload, existing)
