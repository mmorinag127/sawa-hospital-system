"""Question identity, branch answers and managed resumption; synthetic provider only."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from experiments.agent_assistant.app import create_app, NewRun
from experiments.agent_assistant.conversation import question_result, pending_question, check_answer, conversation
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.mcp_server import build_mcp
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_trial import prepared, Event, FakeStream


@pytest.mark.parametrize('raw', [
    {'question':' '}, {'question':'質問','options':['同じ','同じ']},
    {'question':'質問','options':[1]}, {'question':'質問','options':['']},
    {'question':'質問','options':['x']*9}, {'question':'質問','extra':1},
])
def test_invalid_questions(raw):
    with pytest.raises(ValueError): question_result(raw,'q')


def test_stale_and_unlisted_answers_are_rejected():
    result=question_result({'question':'どちらですか','options':['再確認','中止']},'q1')
    events=[{'id':1,'kind':'tool_result','payload':{'tool':'ask_operator','result':result}}]
    check_answer(events,'再確認','q1','再確認')
    check_answer(events,'原本をもう一度確認してください','q1')
    with pytest.raises(Conflict): check_answer(events,'再確認','old','再確認')
    with pytest.raises(ValueError): check_answer(events,'勝手に確定','q1','勝手に確定')
    events.append({'id':2,'kind':'operator_reply','payload':{'message':'再確認'}})
    assert pending_question(events) is None
    with pytest.raises(Conflict): check_answer(events,'再確認','q1','再確認')
    assert [m['role'] for m in conversation(events)]==['assistant','user']


def test_answers_resume_exact_calls_and_ask_next_question(tmp_path):
    store,sandbox,tools,run=prepared(tmp_path,('shift',))
    store.update(run['id'],session_id='synthetic-session')
    sent=[]
    class Events:
        index=0
        async def stream(self,_):
            self.index+=1
            if self.index==1:
                return FakeStream([Event(type='agent.custom_tool_use',id='q1',name='ask_operator',input={'question':'不足分はどうしますか','options':['条件を見直す','今回は中止']})])
            # Future-only streams cannot repeat an already answered tool call.
            return FakeStream([Event(type='agent.custom_tool_use',id=f'q{self.index}',name='ask_operator',input={'question':'見直す内容を教えてください'})])
        async def send(self,_,**kwargs):sent.extend(kwargs['events'])
    async def boot(_):pass
    sandbox.start=boot
    managed=Managed(store,sandbox,tools,build_mcp(tools),{},client=SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace(events=Events()))))
    async def scenario():
        await managed.consume(run['id'])
        assert pending_question(store.events(run['id']))['question_id']=='q1'
        await managed.reply(run['id'],'条件を見直す','q1','条件を見直す')
        await managed.tasks[run['id']]
        assert pending_question(store.events(run['id']))['question_id']=='q2'
        with pytest.raises(Conflict): await managed.reply(run['id'],'古い回答','q1')
        assert len(sent)==1
        await managed.reply(run['id'],'午前の人数を再確認してください','q2')
        await managed.tasks[run['id']]
        assert sent[-1]['custom_tool_use_id']=='q2'
        assert '午前の人数' in sent[-1]['content'][0]['text']
        assert pending_question(store.events(run['id']))['question_id']=='q3'
        assert store.get(run['id'])['status']=='waiting_input'
        assert store.latest_event(run['id'],'error') is None
        store.update(run['id'],status='stopped')
        with pytest.raises(Conflict): await managed.reply(run['id'],'停止後の回答','q2')
    asyncio.run(scenario())


def test_run_uses_admin_limit_without_operator_budget(tmp_path):
    assert NewRun(request_key='test-12345',task_id='hospital_pending_faxes').cap_cents is None
    class Client:
        async def close(self):pass
    app=create_app({},tmp_path,managed_client=Client())
    store=app.state.store;store.set_budget(1000)
    async def start(run):store.update(run['id'],status='running')
    app.state.managed.start=start
    app.state.sandbox.prepare=lambda run:None
    with TestClient(app) as client:
        client.get('/')
        result=client.post('/runs',json={'request_key':'test-12345','task_id':'hospital_pending_faxes'})
        assert result.status_code==201,result.text
        assert result.json()['cap']==100
        assert client.get('/admin').status_code==200
