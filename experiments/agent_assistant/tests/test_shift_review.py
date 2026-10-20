from copy import deepcopy
import asyncio
from experiments.agent_assistant.shift_review import build_shift_review
from experiments.agent_assistant.tests.test_selected_tasks import shift


def source():
    data={'start_date':'2026-08-01','end_date_exclusive':'2026-08-03','assignments':[{'staff_id':1,'staff_name':'試験A','work_date':'2026-08-01','position_id':1,'shift_template_id':1,'position':'調理','shift_template':'日勤'}],
          'statuses':[{'staff_id':1,'staff_name':'試験A','work_date':'2026-08-01','status':'working','work_kind':'position'},{'staff_id':1,'staff_name':'試験A','work_date':'2026-08-02','status':'off','work_kind':'off'}]}
    review={'evidence':[{'operation':'shift_get_run','data':data},{'operation':'shift_get_validation','data':{'blocking':False,'warnings':[]}},
        {'operation':'shift_get_constraints','data':{'items':[{'staff_id':1,'work_date':'2026-08-02','status':'request_off','strength':'soft'}]}},
        {'operation':'shift_get_demands','data':{'items':[{'work_date':'2026-08-01','position_id':1,'shift_template_id':1,'required_count':1}]}}]}
    return review,data


def test_review_shows_exact_cells_wishes_shortages_and_reference_changes():
    review,data=source();old=deepcopy(data);old['assignments']=[];old['statuses'][0].update(status='off',work_kind='off')
    original=deepcopy(review)
    result=build_shift_review(review,{'state':'available','data':old})
    assert result['matrix'][0]['cells'][0]['text']=='調理 / 日勤'
    assert result['matrix'][0]['cells'][1]['text']=='休み'
    assert result['constraints'][0]['反映']=='反映済み' and result['reflected']==1
    assert result['shortages']==[] and len(result['changes'])==1
    assert result['changes'][0]['変更前']=='休み'
    assert review==original


def test_missing_status_and_validation_do_not_become_holidays_or_passes():
    review,data=source();data['statuses']=[];review['evidence']=[review['evidence'][0]]
    result=build_shift_review(review)
    assert result['matrix'][0]['cells'][1]['text']=='未確認'
    assert result['validation_known'] is False and result['demand_known'] is False
    assert result['comparison_available'] is False


def test_unfulfilled_soft_wish_and_real_shortage_are_visible():
    review,data=source();review['evidence'][2]['data']['items'][0]['work_date']='2026-08-01'
    review['evidence'][3]['data']['items'][0]['min_count']=2
    result=build_shift_review(review)
    assert result['reflected']==0 and result['unreflected']==1
    assert result['constraints'][0]['反映']=='未反映'
    assert result['shortages'][0]['不足人数']==1


def test_ambiguous_confirmed_references_are_never_picked(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        native.data['/solve-runs']=[{'id':i,'target_month':'2026-11','start_date':'2026-11-01','end_date_exclusive':'2026-12-01','confirmed':True} for i in [1,2]]
        await tools.flow.capture_shift_reference(run)
        event=next(e for e in store.events(run['id']) if e['kind']=='shift_reference')
        assert event['payload']=={'state':'ambiguous'}
        assert '/solve-runs/1' not in native.reads and '/solve-runs/2' not in native.reads
    asyncio.run(scenario())


def test_incomplete_validation_is_not_a_pass_or_an_empty_wish_list():
    review,data=source();review['evidence']=[review['evidence'][0],{'operation':'shift_get_validation','data':{}}]
    result=build_shift_review(review)
    assert not result['validation_known'] and not result['constraints_known']


def test_removed_staff_records_are_included_in_changes():
    review,data=source();prior=deepcopy(data);prior['statuses'].append({'staff_id':2,'staff_name':'試験B','work_date':'2026-08-01','status':'off','work_kind':'off'})
    result=build_shift_review(review,{'state':'available','data':prior})
    assert any(row['変更前']=='休み' and row['変更後']=='記録なし' for row in result['changes'])
