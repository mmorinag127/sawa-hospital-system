from copy import deepcopy
import importlib.util
from pathlib import Path
import json
from io import BytesIO
from datetime import date
import hashlib
from zipfile import ZipFile

import pytest
from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface, database_snapshot
from openpyxl import Workbook
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "prepare_invoice_column_correction", ROOT / "scripts/prepare_invoice_column_correction.py")
correction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(correction)


@pytest.fixture
def trial_invoice(tmp_path, monkeypatch):
    from src.services import agent_trial_transport as local
    from src.services import invoice_column_correction_service as service
    reviewed = json.loads((ROOT/'docs/fureai-invoice-reviewed-columns-20261112.json').read_text())
    sources = [*reviewed['source_columns'], *reviewed['excluded_source_columns']]
    fields = ['qty.regular_x','qty.daycare_x','qty.staff_x','qty.no_meat_x','qty.no_fish_x','qty.no_fried_x','qty.change_1_x']
    facility = {'facility_id':'SYNTHETIC-F', 'facility_name':'Original synthetic facility', 'areas':[],
        'fax_template':{'columns':[{**c,'role':'quantity','name':field} for c,field in zip(sources,fields,strict=True)]},
        'invoice_template':{'template_uri':str(ROOT/'backend/tests/fixtures/invoice-original-e0edc384.xlsx'),
            'sheet_name':'松岡会','include_menu_name':False,'columns':[
                *deepcopy(reviewed['invoice_columns'][:3]),
                {'name':'Old floor','header':'Old floor','source':'quantity','diet_type':'regular','area_id':'2F','column_index':4},
                deepcopy(reviewed['invoice_columns'][-1])]}}
    sheet = {'fields':['date_mmdd','daypart','menu',*fields,'remarks'],
        'header':['日付','区分','メニュー',*[c['header'] for c in sources],'備考欄'],
        'rows':[['11/15','朝','Synthetic dish','17','23','31','5','7','11','13','operator note']]}
    candidate = {'configured_input':{'facility':facility},'output_lines':[
        {'date':date(2026,11,15),'daypart':'朝','menu_name':'Synthetic dish','diet_type':diet,'area_id':'X','quantity_original':qty}
        for diet,qty in [('regular',17),('daycare',23),('staff',31)]]}
    context = {'order_id':'OA','saved_sheet_id':'S1','source_sha256':'a'*64,'sheet_sha256':'original-sheet','candidate_sha256':'accepted'}
    body = {key:deepcopy(reviewed[key]) for key in ('source_columns','excluded_source_columns','invoice_columns')}
    body.update(saved_sheet_id='S1',source_sha256='a'*64,reason='Synthetic original and saved-sheet review')
    (tmp_path/'trial-manifest.json').write_text('{"purpose":"sawa-agent-isolated-trial"}')
    monkeypatch.setattr(local,'enabled',lambda:True)
    monkeypatch.setattr(local,'configuration',lambda:{'root':str(tmp_path)})
    monkeypatch.setattr(service,'_binding',lambda oid,accepted:(deepcopy(context),deepcopy(sheet)))
    return service, candidate, sheet, context, body, tmp_path


def test_trial_correction_preserves_inputs_and_renders_three_distinct_quantities(trial_invoice):
    service,candidate,sheet,context,body,root = trial_invoice
    from src.services import output_builder as output
    before = deepcopy((candidate,sheet,body))
    order = {'facility':'SYNTHETIC-F','lines':candidate['output_lines']}
    facility = candidate['configured_input']['facility']
    with pytest.raises(ValueError,match='delivery_quantity_mapping_unresolved'):
        output._build_delivery_rows(order,facility['invoice_template'],{},facility,allow_ocr_menu_meta=False)
    template = service.trial_invoice_template('OA',candidate,body)
    assert template == service.trial_invoice_template('OA',candidate)
    assert service.trial_invoice_template('OB',candidate) is None
    rows = output._build_delivery_rows(order,template,{},facility,allow_ocr_menu_meta=False)
    assert [{name:r[name] for name in ('常食','通所','職員')} for r in rows] == [{'常食':17,'通所':23,'職員':31}]
    path = root/'actual-three-quantities.xlsx'
    output._write_delivery_note(path,rows,template['columns'],template['template_uri'],False,'松岡会',facility['facility_name'])
    workbook = load_workbook(path)
    ws = workbook['松岡会']
    assert [ws.cell(8,i).value for i in (4,5,6)] == ['常食','通所','職員']
    assert all(f'{letter}8:{letter}11' in ws.merged_cells for letter in ('D','E','F'))
    assert ws['D8'].alignment.vertical=='center' and ws['D8'].alignment.wrap_text
    assert [ws.cell(12,i).value for i in (4,5,6)] == [17,23,31]
    workbook.close()
    assert (candidate,sheet,body) == before


@pytest.mark.parametrize('fault',['pdf','sheet','aggregate','header','omission','duplicate','saved_header','unmapped_line'])
def test_trial_correction_rejects_guessed_or_changed_correspondence(trial_invoice,fault):
    service,candidate,sheet,context,body,root = trial_invoice
    if fault=='pdf': body['source_sha256']='b'*64
    elif fault=='sheet': body['saved_sheet_id']='other'
    elif fault=='aggregate': body['invoice_columns'][3].pop('area_id')
    elif fault=='header': body['source_columns'][0]['header']='Guessed'
    elif fault=='omission': body['excluded_source_columns']=[]
    elif fault=='duplicate': body['source_columns'].append(deepcopy(body['source_columns'][0]))
    elif fault=='saved_header': sheet['header'][3]='Another header'
    else: candidate['output_lines'].append({**candidate['output_lines'][0],'diet_type':'mixer'})
    with pytest.raises(ValueError): service.trial_invoice_template('OA',candidate,body)
    assert not (root/'owner-invoice-columns'/'OA.json').exists()


def test_trial_correction_revalidates_binding_and_annotation_and_stays_isolated(trial_invoice,monkeypatch):
    service,candidate,sheet,context,body,root = trial_invoice
    from src.services import agent_trial_transport as local
    service.trial_invoice_template('OA',candidate,body)
    context['sheet_sha256']='edited-after-registration'
    with pytest.raises(ValueError,match='accepted_input_changed'):
        service.trial_invoice_template('OA',candidate)
    context['sheet_sha256']='original-sheet'
    path=root/'owner-invoice-columns'/'OA.json'
    saved=json.loads(path.read_text())
    saved['invoice_template']['columns'][3]['area_id']='2F'
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='registration_changed'):
        service.trial_invoice_template('OA',candidate)
    monkeypatch.setattr(local,'enabled',lambda:False)
    assert service.trial_invoice_template('OA',candidate) is None
    with pytest.raises(ValueError,match='isolated_trial_only'):
        service.trial_invoice_template('OA',candidate,body)
    from src.api.orders import register_trial_invoice_columns
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        register_trial_invoice_columns('OA',body)
    assert exc.value.status_code==404


def test_real_confirmed_read_uses_correction_without_rewriting_saved_lineage(owned_bagging,tmp_path,monkeypatch):
    from test_modernization_real_daily_delivery_html import confirm
    from src.services import order_service as orders, output_builder as output, agent_trial_transport as local
    from src.services import invoice_column_correction_service as service
    from src.models.order import Order
    from src.models.document import OrderDocument
    from src.models.order_sheet_draft import OrderSheetDraft
    fixture=owned_bagging
    saved=fixture.saved()
    saved['header']=['Date','Meal','Category','Menu','Quantity','No meat','No fish','Staff']
    with fixture.session_scope() as session:
        session.get(OrderSheetDraft,fixture.draft_id).draft_sheet_json=deepcopy(saved)
    for column in fixture.authored['fax_template']['columns']:
        if column.get('role')=='quantity':
            index=column['source_index']
            column.update(name=saved['fields'][index],header=saved['header'][index])
    fixture.authored['invoice_template']['template_uri']=str(ROOT/'backend/tests/fixtures/invoice-original-e0edc384.xlsx')
    fixture.authored['invoice_template']['columns'][-1]['area_id']='2F'
    fixture.republish()
    with fixture.session_scope() as session:
        orders._record_current_order_version(session,session.get(Order,fixture.order_id),session.get(OrderDocument,'UNIT-DOC'))
    candidate=confirm(fixture)
    before=database_snapshot()
    saved=fixture.saved()
    (tmp_path/'trial-manifest.json').write_text('{"purpose":"sawa-agent-isolated-trial"}')
    monkeypatch.setattr(local,'enabled',lambda:True)
    monkeypatch.setattr(local,'configuration',lambda:{'root':str(tmp_path)})
    context,_=service._binding(fixture.order_id,candidate)
    source=candidate['configured_input']['facility']['fax_template']['columns']
    qty=[{key:c[key] for key in ('source_index','header','diet_type','area_id')} for c in source if c.get('role')=='quantity']
    columns=deepcopy(candidate['configured_input']['facility']['invoice_template']['columns'])
    columns[-1].update(area_id='X',name=qty[0]['header'],header=qty[0]['header'])
    body={'saved_sheet_id':fixture.draft_id,'source_sha256':context['source_sha256'],
          'source_columns':qty[:1],'excluded_source_columns':qty[1:],'invoice_columns':columns,'reason':'Synthetic original review'}
    service.trial_invoice_template(fixture.order_id,candidate,body)
    preview=output.build_delivery_preview(fixture.order_id,include_diagnostics=False)
    quantity=next(c for c in preview['columns'] if c['source']=='quantity')
    assert quantity['area_id']=='X' and quantity['name']==qty[0]['header']
    assert database_snapshot()==before
    assert fixture.saved()==saved


def test_reviewed_invoice_correction_preserves_identity_and_ocr():
    source = {"source_index": 5, "header": "通所", "diet_type": "daycare", "area_id": "X"}
    facility = {"facility_id": "FAC-test", "facility_name": "original name", "areas": [],
                "fax_template": {"columns": [{**source, "role": "quantity",
                                             "semantic": {"aggregation_role": "include"}}]},
                "invoice_template": {"template_uri": "original.xlsx", "columns": [
                    {"name": "old", "source": "quantity", "diet_type": "regular"}]}}
    before = deepcopy(facility)
    columns = [{"name": "通所", "source": "quantity", "diet_type": "daycare", "area_id": "X"}]
    result = correction.prepare_correction(facility, source_columns=[source], invoice_columns=columns)
    assert facility == before
    assert result["fax_template"] == before["fax_template"]
    assert result["facility_name"] == before["facility_name"]
    assert result["areas"] == before["areas"]
    assert result["invoice_template"]["template_uri"] == "original.xlsx"
    for bad in ({**source, "header": "other"}, {**source, "source_index": 6}):
        with pytest.raises(ValueError, match="reviewed_source_columns_changed"):
            correction.prepare_correction(facility, source_columns=[bad], invoice_columns=columns)
    with pytest.raises(ValueError, match="explicit_one_to_one"):
        correction.prepare_correction(facility, source_columns=[source],
                                      invoice_columns=[{**columns[0], "area_id": None}])


def test_slot_heading_replaces_stale_template_without_touching_body():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    ws.cell(4, 1, "facility title")
    ws.cell(8, 4, "常食2F")
    ws.cell(8, 7, "ミキサー")
    ws.merge_cells("G8:H10")
    ws.cell(11, 7, "2F")
    ws.cell(11, 8, "3F")
    ws.cell(12, 7, 123)
    columns = [{"name": "日付", "column_index": 1},
               {"name": "常食", "column_index": 4},
               {"name": "通所", "column_index": 5},
               {"name": "職員", "column_index": 6},
               {"name": "備考", "column_index": 9}]
    _apply_delivery_configured_headers(ws, 8, columns,
        {column["name"]: column["column_index"] for column in columns},
        data_start_row=12, replace_header_band=True)
    assert [ws.cell(8, index).value for index in (4, 5, 6)] == ["常食", "通所", "職員"]
    assert all(ws.cell(row, col).value in (None, "") for row in range(8, 12) for col in (7, 8))
    assert ws.cell(4, 1).value == "facility title"
    assert ws.cell(12, 7).value == 123


def test_heading_merge_into_saved_quantity_is_rejected():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    ws.merge_cells("D8:D12")
    with pytest.raises(ValueError, match="crosses_data_boundary"):
        _apply_delivery_configured_headers(ws, 8, [{"name": "通所", "column_index": 4}],
                                           {"通所": 4}, data_start_row=12, replace_header_band=True)
    assert "D8:D12" in ws.merged_cells


def test_future_original_labels_keep_history_and_existing_aggregation():
    review = json.loads((ROOT / "docs/kasuga-future-reviewed-columns-20261112.json").read_text())
    columns = [{"source_index": patch["source_index"], **patch["expected"]}
               for patch in review["source_label_patches"]]
    invoice = [{"column_index": patch["column_index"], "source": "quantity", **patch["expected"]}
               for patch in review["invoice_label_patches"]]
    soft = {"column_index": 6, "source": "quantity", "name": "軟菜", "diet_type": "soft"}
    invoice.append(soft)
    facility = {"facility_id": "FAC00003", "facility_name": "unchanged", "areas": [],
                "fax_template": {"columns": columns, "main_ocr_row_fields": [column["name"] for column in columns]},
                "fax_template_override": {"columns": deepcopy(columns)},
                "invoice_template": {"columns": invoice}}
    original = deepcopy(facility)
    result = correction.prepare_source_labels(facility, review)
    assert facility == original
    assert result["facility_name"] == "unchanged"
    assert result["invoice_template"]["columns"][-1] == soft
    assert result["areas"] == [{"id": "hana", "name": "花"}, {"id": "tsuki", "name": "月"}]
    assert all(column["area_id"] in {"hana", "tsuki"} for column in result["fax_template"]["columns"])
    assert {column["header"] for column in result["fax_template"]["columns"]} == {"花", "月"}
    assert result["fax_template_override"]["columns"] == result["fax_template"]["columns"]
    assert not any("_2f" in name or "_3f" in name for name in result["fax_template"]["main_ocr_row_fields"])
    assert all("aliases" not in area for area in result["areas"])


def test_fixed_slot_quantity_headings_keep_area_identity():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    columns = [{"name": "ミキサー 花", "header": "ミキサー", "source": "quantity", "column_index": 7},
               {"name": "ミキサー 月", "header": "ミキサー", "source": "quantity", "column_index": 8}]
    _apply_delivery_configured_headers(ws, 8, columns,
        {column["name"]: column["column_index"] for column in columns},
        data_start_row=12, replace_header_band=True)
    assert ws.cell(8, 7).value == "ミキサー 花"
    assert ws.cell(8, 8).value == "ミキサー 月"


def test_final_xlsx_preserves_separate_headings_and_untouched_merges(tmp_path):
    from src.services.output_builder import (
        _apply_delivery_configured_headers, _patch_template_package_with_workbook_values,
    )
    workbook = Workbook()
    ws = workbook.active
    ws.merge_cells("A1:I1")
    ws.cell(1, 1, "unchanged title")
    ws.merge_cells("G8:H10")
    ws.cell(8, 7, "old shared heading")
    ws.cell(12, 7, 17)
    ws.cell(12, 8, 23)
    template = BytesIO()
    workbook.save(template)
    columns = [{"name": "花", "source": "quantity", "column_index": 7},
               {"name": "月", "source": "quantity", "column_index": 8}]
    _apply_delivery_configured_headers(ws, 8, columns, {"花": 7, "月": 8},
                                       data_start_row=12, replace_header_band=True)
    output = tmp_path / "delivery.xlsx"
    _patch_template_package_with_workbook_values(workbook, template_bytes=template.getvalue(),
        output_path=output, modified_worksheets={ws.title}, sync_removed_merges=True, header_merge_rows=(8,11))
    restored = load_workbook(output)
    actual = restored.active
    assert [actual.cell(8, col).value for col in (7, 8)] == ["花", "月"]
    assert [actual.cell(12, col).value for col in (7, 8)] == [17, 23]
    assert {str(merged) for merged in actual.merged_cells.ranges} == {"A1:I1","G8:G11","H8:H11"}
    assert actual.cell(1, 1).value == "unchanged title"
    restored.close()


@pytest.mark.parametrize('merge',['D8:E11','D8:D12','D12:D14'])
def test_only_independent_header_band_merges_can_be_added(tmp_path,merge):
    from src.services.output_builder import _patch_template_package_with_workbook_values
    workbook=Workbook()
    workbook.active.cell(12,4,123)
    template=BytesIO()
    workbook.save(template)
    workbook.active.merge_cells(merge)
    with pytest.raises(ValueError,match='merge_addition_unsupported'):
        _patch_template_package_with_workbook_values(workbook,template_bytes=template.getvalue(),
            output_path=tmp_path/'rejected.xlsx',sync_removed_merges=True,header_merge_rows=(8,11))


@pytest.mark.parametrize("facility", ["fureai", "kasuga"])
def test_original_workbook_invoice_output_and_readback(tmp_path, monkeypatch, facility):
    from src.services import output_builder
    raw = (ROOT / "backend/tests/fixtures/invoice-original-e0edc384.xlsx").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "e0edc3849dacfaabbeb81eb9329cea7baa4cd994c16ef69e187716981bb274ad"
    if facility == "fureai":
        review = json.loads((ROOT / "docs/fureai-invoice-reviewed-columns-20261112.json").read_text())
        columns = review["invoice_columns"]
        name = "介護老人保健施設ふれあいの丘"
        quantities = {"常食": 67, "通所": None, "職員": None}
    else:
        review = json.loads((ROOT / "docs/kasuga-future-reviewed-columns-20261112.json").read_text())
        columns = [
            {"name": "日付", "source": "date", "column_index": 1},
            {"name": "区分", "source": "daypart", "column_index": 2},
            {"name": "献立", "source": "menu_display", "column_index": 3},
            {"name": "軟菜", "source": "quantity", "diet_type": "soft", "column_index": 6},
            {"name": "備考", "source": "note", "column_index": 9},
            *[{"source": "quantity", "column_index": patch["column_index"], **patch["replacement"]}
              for patch in review["invoice_label_patches"]],
        ]
        name = "春日苑 松茂"
        quantities = {"常食 花": 7, "常食 月": 7, "軟菜": None, "ミキサー 花": 2, "ミキサー 月": 3}
    monkeypatch.setattr(output_builder, "load_bytes_from_uri", lambda uri: raw)
    row = {"date": date(2026, 11, 15), "daypart": "朝", "menu_name": "ジャーマンポテト",
           "menu_display": "ジャーマンポテト", "menu_category": "副菜1", "note": None, **quantities}
    path = tmp_path / "delivery.xlsx"
    output_builder._write_delivery_note(path, [row], columns, "fixture.xlsx", False, "松岡会", name)
    with ZipFile(BytesIO(raw)) as original, ZipFile(path) as result:
        assert result.read("xl/worksheets/sheet2.xml") == original.read("xl/worksheets/sheet2.xml")
    workbook = load_workbook(path)
    ws = workbook["松岡会"]
    assert {column["name"]: ws.cell(8, column["column_index"]).value
            for column in columns if column["source"] == "quantity"} == {key: key for key in quantities}
    assert all(f'{letter}8:{letter}11' in ws.merged_cells for letter in ('D','E','F'))
    assert {column["name"]: ws.cell(12, column["column_index"]).value
            for column in columns if column["source"] == "quantity"} == quantities
    assert ws.cell(4, 1).value == name
    workbook.close()
