"""Real controller/question ledger and synthetic native API; no model or production DB."""
import asyncio
from copy import deepcopy

import httpx
import pytest

from experiments.agent_assistant.conversation import pending_question
from experiments.agent_assistant.tests.test_business_workflow import attach
from experiments.agent_assistant.tests.test_selected_tasks import shift
from experiments.agent_assistant.store import Conflict


def environment(tmp_path, task='shift_absence', *, blocking=False):
    store,native,tools,run=shift(tmp_path,task)
    context=run['task_inputs']
    if task=='shift_absence':
        context['inputs']['absence_date']='2026-11-02'
        context['targets']=[{'key':'1','label':'合成人員','data':{'id':1,'name':'合成人員'}}]
        store.update(run['id'],task_inputs=context)
    native.data['/solve-runs']={'items':[]}
    native.data['/constraints']={'collection_revision':1,'items':[{'id':10,'revision':1,'staff_id':1,'staff_name':'合成人員','work_date':'2026-11-02','status':'request_work','strength':'hard','weight':10,'reason':'既存の指定','shift_template_id':None}]}
    native.data['/optimization-settings']={'settings_revision':'a'*64,'forbid_half_day_on_weekdays':True,'forbid_half_day_on_saturdays':True}
    native.data['/solve-runs/501']['command_precondition']={'candidate_id':77,'candidate_revision':'a'*64,'input_revision':'b'*64}
    native.blocking=blocking;native.reject=None
    request=native.request
    async def transport(run,domain,method,path,**kwargs):
        if path=='/rule-preflight':
            return httpx.Response(200,json={'blocking':native.blocking,'issues':[{'message':'休日と勤務条件が同時に成立しません','suggestions':[{'label':'土曜の自動半日勤務を許可する','settings_patch':{'forbid_half_day_on_saturdays':False}}]}] if native.blocking else []})
        if path=='/solve' and method=='POST':
            native.writes.append((path,deepcopy(kwargs)));return httpx.Response(200,json={'solve_run_id':501})
        if path=='/constraints/10' and method=='PUT':
            if native.reject:return httpx.Response(native.reject,json={'detail':'changed'})
            body=kwargs['body'];assert body['revision']==native.data['/constraints']['items'][0]['revision']
            assert body['collection_revision']==native.data['/constraints']['collection_revision']
            native.writes.append((path,deepcopy(kwargs)))
            item={**body,'id':10,'revision':body['revision']+1}
            native.data['/constraints']['items']=[item];native.data['/constraints']['collection_revision']+=1
            return httpx.Response(200,json={'item':item,'collection_revision':native.data['/constraints']['collection_revision']})
        if path=='/optimization-settings' and method=='PUT':
            body=kwargs['body'];assert body['settings_revision']==native.data[path]['settings_revision']
            native.writes.append((path,deepcopy(kwargs)))
            native.data[path]={**body,'settings_revision':'b'*64};native.blocking=False
            return httpx.Response(200,json=native.data[path])
        return await request(run,domain,method,path,**kwargs)
    native.request=transport
    engine,run=attach(store,native,tools,store.get(run['id']))
    engine.kick=lambda *a,**kw:None
    return store,native,tools,run,engine


def answer(store,engine,run,label):
    question=pending_question(store.events(run['id']))
    assert question and label in question['options']
    assert engine.shift_questions.answer(store.get(run['id']),label,question['question_id'],label)


def test_absence_existing_work_condition_questions_confirm_then_finish_without_model(tmp_path):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path)
        assert await engine.advance(run['id']) is False
        question=pending_question(store.events(run['id']))
        assert '勤務希望' in question['text'] and '合成人員' in question['text']
        assert not native.writes
        answer(store,engine,run,'この日の条件を欠勤に変更する')
        # A single transaction stores answer and continuation; restarted controller
        # can enter this phase without any provider session or extra user action.
        assert engine.state(run['id'])['phase']=='shift_adjustment'
        assert await engine.advance(run['id']) is False
        assert not native.writes
        answer(store,engine,run,'この変更を反映して作り直す')
        assert await engine.advance(run['id']) is True
        assert native.data['/constraints']['items'][0]['status']=='unavailable'
        assert await engine.advance(run['id']) is False
        review=tools.flow.review(run['id']);assert review['state']=='awaiting_approval'
        assert native.data['/solve-runs/501']['confirmed'] is False
        await tools.flow.decide(store.get(run['id']),review['id'],True,'')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status']=='completed'
        assert sum(path=='/constraints/10' for path,_ in native.writes)==1
        assert not engine.providers.started
    asyncio.run(scenario())


@pytest.mark.parametrize('task',['shift_next_month','shift_wishes','shift_absence'])
def test_infeasible_preflight_asks_and_applies_exact_proposed_patch_after_confirmation(tmp_path,task):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path,task,blocking=True)
        if task=='shift_absence':native.data['/constraints']['items'][0]['status']='unavailable'
        assert await engine.advance(run['id']) is False
        assert engine.projection(store.get(run['id']))['next_action']=='answer'
        assert not any(path=='/solve' for path,_ in native.writes)
        answer(store,engine,run,'土曜の自動半日勤務を許可する')
        assert await engine.advance(run['id']) is False
        assert native.data['/optimization-settings']['forbid_half_day_on_saturdays'] is True
        answer(store,engine,run,'この変更を反映して作り直す')
        assert await engine.advance(run['id']) is True
        saved=native.data['/optimization-settings']
        assert saved['forbid_half_day_on_saturdays'] is False
        assert saved['forbid_half_day_on_weekdays'] is True
        assert await engine.advance(run['id']) is False
        review=tools.flow.review(run['id']);assert review['state']=='awaiting_approval'
        await tools.flow.decide(store.get(run['id']),review['id'],True,'')
        await engine.advance(run['id'])
        assert store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


def test_unapproved_settings_and_constraint_changes_cannot_use_server_authorization(tmp_path):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path)
        with pytest.raises(Conflict):
            await engine.call(run,'shift_save_settings',{'parameters':{'body':{'settings_revision':'a'*64},'reason':'forged'}},'forged')
        assert not native.writes
        await engine.advance(run['id'])
        question=pending_question(store.events(run['id']))
        assert engine.shift_questions.answer(store.get(run['id']),'この日の条件を欠勤に変更する','stale-question','この日の条件を欠勤に変更する') is False
        assert pending_question(store.events(run['id']))['question_id']==question['question_id']
        assert not native.writes
    asyncio.run(scenario())


def test_known_revision_rejection_becomes_new_question_without_overwrite_or_repeated_write(tmp_path):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path)
        await engine.advance(run['id']);answer(store,engine,run,'この日の条件を欠勤に変更する')
        await engine.advance(run['id']);answer(store,engine,run,'この変更を反映して作り直す')
        native.reject=409
        assert await engine.advance(run['id']) is False
        assert '変更は保存されませんでした' in pending_question(store.events(run['id']))['text']
        assert engine.state(run['id'])['phase']=='question' and not native.writes
        assert native.data['/constraints']['items'][0]['status']=='request_work'
    asyncio.run(scenario())


def test_eight_registered_staff_options_are_all_available_and_admin_stop_wins(tmp_path):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path)
        items=[{'label':f'合成職員{i}','action':'retry'} for i in range(8)]
        await engine.shift_questions.page(run,'eight','対象を選んでください',items)
        assert len(pending_question(store.events(run['id']))['options'])==8
        store.fence(run['id'])
        with pytest.raises(Conflict):
            engine.shift_questions.answer(store.get(run['id']),'合成職員7',pending_question(store.events(run['id']))['question_id'],'合成職員7')
        assert not native.writes
    asyncio.run(scenario())


def test_solver_infeasible_result_is_a_business_question_without_fabricating_a_candidate(tmp_path):
    async def scenario():
        store,native,tools,run,engine=environment(tmp_path,'shift_wishes')
        native.data['/solve-runs/501'].update(status='failed',selected_candidate_id=None,candidates=[],solver_status='INFEASIBLE',violations=[{'message':'登録された必須の勤務条件が両立しません'}])
        assert await engine.advance(run['id']) is False
        question=pending_question(store.events(run['id']))
        assert '必須の勤務条件が両立しません' in question['text']
        assert '登録済みの希望休・勤務条件を見直す' in question['options']
        assert not tools.flow.review(run['id'])
        assert not any(path.endswith('/confirm') for path,_ in native.writes)
        assert store.get(run['id'])['status']=='waiting_input'
    asyncio.run(scenario())
