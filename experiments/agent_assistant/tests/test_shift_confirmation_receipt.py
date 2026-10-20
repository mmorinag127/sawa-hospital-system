"""Native confirmation renews metadata, never the reviewed schedule or inputs."""
from copy import deepcopy
import pytest
from experiments.agent_assistant.task_flow import shift_confirmation_content
from experiments.agent_assistant.store import Conflict


def pair():
    state={'assignments':[{'staff_id':1,'work_date':'2026-08-01','shift_template_id':1,'position_id':2,'is_locked':False,'source':'solver'}],
           'statuses':[{'staff_id':1,'work_date':'2026-08-01','status':'working','work_kind':'position','shift_template_id':1}]}
    before={'selected_candidate_id':1,'confirmed':False,'confirmed_at':None,'command_precondition':{'candidate_id':1,'candidate_revision':'a'*64,'input_revision':'b'*64},
            **deepcopy(state),'candidate_validation':{'blocking':False,'warnings':[]},
            'candidates':[{'id':1,'score_breakdown':{'objective':1,'generation_time_snapshot':{'candidate_state':deepcopy(state)}}}]}
    after=deepcopy(before);after.update(confirmed=True,confirmed_at='2026-10-20T00:00:00',command_precondition={'candidate_id':1,'candidate_revision':'c'*64,'input_revision':'d'*64})
    after['candidates'][0]['score_breakdown']['confirmed_validation_snapshot']={'version':1,'rule_source':{'tables':{}},'time_rows':[], 'candidate_state':deepcopy(state),'validation':deepcopy(before['candidate_validation'])}
    return before,after


def test_native_confirmation_metadata_does_not_stale_review():
    before,after=pair();original=deepcopy(after)
    assert shift_confirmation_content(before)==shift_confirmation_content(after,before=before)
    assert after==original


@pytest.mark.parametrize('change',['assignment','status','validation','generation','score','candidate','snapshot_assignment','snapshot_validation'])
def test_business_or_validation_changes_remain_blocked(change):
    before,after=pair()
    if change=='assignment':after['assignments'][0]['position_id']=9
    if change=='status':after['statuses'][0]['status']='off'
    if change=='validation':after['candidate_validation']['blocking']=True
    if change=='generation':after['candidates'][0]['score_breakdown']['generation_time_snapshot']['candidate_state']['assignments'][0]['position_id']=9
    if change=='score':after['candidates'][0]['score_breakdown']['objective']=0
    if change=='candidate':after['selected_candidate_id']=2
    if change=='snapshot_assignment':after['candidates'][0]['score_breakdown']['confirmed_validation_snapshot']['candidate_state']['assignments'][0]['position_id']=9
    if change=='snapshot_validation':after['candidates'][0]['score_breakdown']['confirmed_validation_snapshot']['validation']['blocking']=True
    try:actual=shift_confirmation_content(after,before=before)
    except Conflict:return
    assert actual!=shift_confirmation_content(before)


def test_existing_confirmation_snapshot_cannot_be_replaced():
    _,before=pair();after=deepcopy(before)
    after['candidates'][0]['score_breakdown']['confirmed_validation_snapshot']['rule_source']={'tables':{'demands':[]}}
    assert shift_confirmation_content(after,before=before)!=shift_confirmation_content(before)
