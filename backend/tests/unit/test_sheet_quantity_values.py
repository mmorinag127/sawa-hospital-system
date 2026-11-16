from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services import sheet_quantity_value_service as values
from src.services import order_workflow_v2_service as workflow, order_service
from src.services import workflow_v2_sheet_review_service as review


def sheet(raw):
    return {'fields':['date_mmdd','daypart','menu','qty.regular_x','remarks'],
        'header':['日付','区分','メニュー','常食','備考'],
        'rows':[['12/05','昼','Authored menu',raw,'備考は自由文']],
        'hakodate_evidence_projection':{'applied':[{'row_index':0,'field':'qty.regular_x','value':raw}]},
        'ocr_numeric_cell_items':[{'target_row_index':0,'target_col_index':3,'value':raw,'classification':'accepted'}],
        'ocr_numeric_cell_summary':{'raw_ocr_numeric_count':1,'accepted_count':1,'unresolved_count':0}}


@pytest.mark.parametrize('raw',['-','--','?','一','11月20日に配送','14804個だと思います','1/2','1 2','NaN','inf'])
def test_non_numeric_is_reviewable_not_a_quantity_and_original_is_immutable(raw):
    original=sheet(raw); before=deepcopy(original)
    clean=values.editable_sheet(original)
    assert original==before
    assert clean['rows'][0][3]=='' and clean['rows'][0][4]=='備考は自由文'
    assert values.pending_cells(clean)[0]['raw_text']==raw
    assert clean['hakodate_evidence_projection']==original['hakodate_evidence_projection']
    assert clean['ocr_numeric_cell_items'][0]['classification']=='unresolved'
    assert clean['ocr_numeric_cell_summary']['accepted_count']==0
    assert values.editable_sheet(clean)==clean
    assert review._numeric_value(raw) is None


@pytest.mark.parametrize('raw',['0','17','０１','-2','1.5','1,234'])
def test_valid_numeric_values_and_manual_changes_are_not_rewritten(raw):
    original=sheet(raw)
    assert values.editable_sheet(original)==original
    assert values.pending_cells(original)==[]
    assert values.numeric_value(raw) is not None


def test_explicit_numeric_and_blank_confirmation_resolve_without_losing_original():
    clean=values.editable_sheet(sheet('not a number'))
    changed=deepcopy(clean); changed['rows'][0][3]='13'
    assert values.pending_cells(changed)==[]
    assert changed[values.KEY][0]['raw_text']=='not a number'
    clean[values.KEY][0]['confirmed_blank']=True
    assert values.pending_cells(clean)==[] and clean['rows'][0][3]==''
    missing=values.editable_sheet(sheet('not a number')); del missing[values.KEY]
    assert values.pending_cells(missing), 'omitting the review does not silently turn missing quantity into zero'


def test_saved_read_is_only_a_projection_and_both_output_paths_refuse_pending():
    original=sheet('11月20日に配送'); before=deepcopy(original)
    draft=SimpleNamespace(id='saved',order_id='order',template_version_id='template',base_evidence_run_id='evidence',
        draft_sheet_json=original,draft_state='saved',edited_by='operator',edited_at=None,created_at=None)
    serialized=workflow._serialize_saved_sheet(draft)
    assert serialized['sheet']['rows'][0][3]=='' and draft.draft_sheet_json==before
    result=workflow._build_materialization_candidate_for_saved_sheet(session=None,order=SimpleNamespace(id='order'),saved_sheet=draft)
    assert result['error']=='sheet_quantity_review_required'
    result=order_service._build_materialization_candidate_from_draft_record('order',draft_record={'id':'saved','draft_sheet_json':original},
        facility_id='facility',existing_week_code=None,received_at=None,configured_input={})
    assert result['error']=='sheet_quantity_review_required'


def test_anomaly_totals_never_extract_or_concatenate_digits_from_text():
    source=sheet('11月20日に配送')
    source['rows'].append(['12/05','昼','Second menu','2',''])
    report=review.build_sheet_anomaly_report(sheet=source,use_llm=False)
    assert report['computed_context']['day_totals'][0]['total']==2
    assert len(report['quantity_review'])==1
    assert not any(str(w.get('value')) in ('1120','1122','14804') for w in report['warnings'])


def test_review_metadata_cannot_target_another_field():
    clean=values.editable_sheet(sheet('?'))
    clean[values.KEY][0]['field']='remarks'
    with pytest.raises(ValueError,match='identity'):
        values.editable_sheet(clean)


def test_only_current_numeric_policy_unsaved_review_survives_without_a_saved_draft():
    report={'source':'unsaved_sheet','sheet_hash':'source-hash','warnings':[]}
    assert workflow._anomaly_review_for_sheet({'anomaly_review':report},None) is None
    report['quantity_value_version']=1
    assert workflow._anomaly_review_for_sheet({'anomaly_review':report},None)==report
    report['source']='saved_sheet'
    assert workflow._anomaly_review_for_sheet({'anomaly_review':report},None) is None
