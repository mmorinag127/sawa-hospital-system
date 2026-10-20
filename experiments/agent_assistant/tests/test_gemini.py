"""Real local MCP bridge and simulated Gemini HTTP; no external API calls."""
import asyncio
from copy import deepcopy
import json
import re

import httpx
import pytest

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.conversation import pending_question
from experiments.agent_assistant.gemini import Gemini, content, function_result, command_identity
from experiments.agent_assistant.mcp_server import build_mcp
from experiments.agent_assistant.providers import Providers
from experiments.agent_assistant.store import Conflict, Store
from experiments.agent_assistant.tests.test_trial import prepared


def fixture(tmp_path, monkeypatch, handler):
    for key, value in {'GEMINI_API_KEY': 'synthetic-key', 'GEMINI_AGENT_MODEL': 'gemini-test',
        'GEMINI_AGENT_INPUT_USD_PER_MILLION': '1', 'GEMINI_AGENT_OUTPUT_USD_PER_MILLION': '2'}.items(): monkeypatch.setenv(key, value)
    store, sandbox, tools, run = prepared(tmp_path, ('shift',))
    async def boot(_): pass
    sandbox.start = boot
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = Gemini(store, sandbox, tools, build_mcp(tools), {}, client=client)
    return agent, store, sandbox, tools, run


def interaction(ident, calls=(), status='requires_action'):
    return {'id': ident, 'status': status, 'steps': [{'type': 'function_call', 'id': key, 'name': name, 'arguments': args} for key, name, args in calls],
        'usage': {'total_input_tokens': 100, 'total_output_tokens': 20, 'total_thought_tokens': 10}}


def test_gemini_questions_pause_exact_tool_call_and_resume_remaining_batch(tmp_path, monkeypatch):
    requests = []
    replies = [interaction('turn-1', [('q1', 'ask_operator', {'question': '条件を確認しますか？', 'options': ['確認する', '中止']})]),
               interaction('turn-2', [('read', 'shift_get_staff', {'parameters': {}}), ('q2', 'ask_operator', {'question': '変更内容は？'})])]
    async def handler(request):
        assert request.url.host == 'generativelanguage.googleapis.com'
        requests.append(json.loads(request.content) if request.content else None)
        if request.method == 'GET': return httpx.Response(200, json=interaction(request.url.path.rsplit('/',1)[-1], status='completed'))
        return httpx.Response(200, json=replies.pop(0))
    agent, store, sandbox, tools, run = fixture(tmp_path, monkeypatch, handler)
    async def native(*args, **kwargs): return httpx.Response(200, json=[])
    sandbox.request = native
    async def scenario():
        await agent.start(run); await agent.tasks[run['id']]
        question = pending_question(store.events(run['id']))
        assert question['question_id'] == command_identity('turn-1','q1')
        assert len(requests) == 1 and store.get(run['id'])['status'] == 'waiting_input'
        assert requests[0]['background'] is False  # Regular Gemini models reject background=True.
        assert all(t['type']=='function' for t in requests[0]['tools'])
        assert {t['name'] for t in requests[0]['tools']} == {d['name'] for d in tools.definitions(['shift'])}
        await agent.reply(run['id'], '確認する', question['question_id'], '確認する')
        await agent.tasks[run['id']]
        assert requests[1]['previous_interaction_id'] == 'turn-1'
        assert requests[1]['input'][0]['call_id'] == 'q1'
        assert '確認する' in requests[1]['input'][0]['result']
        assert isinstance(requests[1]['input'][0]['result'], str)
        assert pending_question(store.events(run['id']))['question_id'] == command_identity('turn-2','q2')
        assert len(requests) == 2  # No continuation while the second question waits.
        with pytest.raises(Conflict): await agent.reply(run['id'], '古い回答', command_identity('turn-1','q1'))
        await agent.stop(run['id'])
        assert store.get(run['id'])['status'] == 'stopped'
        with pytest.raises(Conflict): await agent.reply(run['id'], '停止後', command_identity('turn-2','q2'))
        await agent.client.aclose()
    asyncio.run(scenario())


def test_gemini_usage_counts_thought_tokens_rounds_cumulative_and_deduplicates(tmp_path, monkeypatch):
    agent, store, _, _, run = fixture(tmp_path, monkeypatch, lambda _: httpx.Response(500))
    value=interaction('one'); value['usage']={'total_input_tokens':10000,'total_output_tokens':10000,'total_thought_tokens':10000}
    agent.record_usage(run['id'], value); agent.record_usage(run['id'], value)
    assert store.get(run['id'])['spent'] == 5
    value['id']='two';agent.record_usage(run['id'], value)
    assert store.get(run['id'])['spent']==10
    assert store.get(run['id'])['usage']['estimated'] is True
    monkeypatch.setenv('GEMINI_AGENT_OUTPUT_USD_PER_MILLION','1000')
    agent.record_usage(run['id'], value)
    assert store.get(run['id'])['spent']==10  # Per-run rate binding cannot drift.
    del value['usage']['total_thought_tokens']
    with pytest.raises(Conflict):agent.record_usage(run['id'], value)


def test_stop_cancels_remote_polling_and_prevents_late_tool_write(tmp_path, monkeypatch):
    created=asyncio.Event();cancelled=[]
    async def handler(request):
        if request.method=='POST' and request.url.path.endswith('/cancel'):
            cancelled.append(True);return httpx.Response(200,json=interaction('remote',status='cancelled'))
        if request.method=='POST': created.set();return httpx.Response(200,json=interaction('remote',status='in_progress'))
        return httpx.Response(200,json=interaction('remote',status='cancelled' if cancelled else 'in_progress'))
    agent,store,sandbox,tools,run=fixture(tmp_path,monkeypatch,handler)
    async def scenario():
        await agent.start(run);await created.wait();await agent.stop(run['id'])
        assert cancelled and agent.tasks[run['id']].done()
        assert store.get(run['id'])['status']=='stopped' and store.get(run['id'])['reserved']==0
        with pytest.raises(Conflict): await tools.execute(run['id'],0,'shift_get_staff',{'parameters':{}},'late')
        await agent.client.aclose()
    asyncio.run(scenario())


def test_unknown_create_outcome_is_not_replayed_or_refunded(tmp_path, monkeypatch):
    calls=[]
    async def handler(request): calls.append(request);raise httpx.ReadTimeout('synthetic secret must never be logged')
    agent,store,_,_,run=fixture(tmp_path,monkeypatch,handler)
    async def scenario():
        await agent.start(run);await agent.tasks[run['id']]
        assert len(calls)==1 and store.get(run['id'])['status']=='stop_pending'
        assert store.get(run['id'])['reserved']==100
        assert 'synthetic secret' not in json.dumps(store.events(run['id']))
        await agent.stop(run['id']);assert len(calls)==1
        await agent.client.aclose()
    asyncio.run(scenario())


def test_explicit_request_rejection_does_not_leave_an_unknown_dispatch(tmp_path,monkeypatch):
    calls=[]
    async def handler(request):
        calls.append(request)
        return httpx.Response(400,json={'error':{'message':'Invalid request'}})
    agent,store,_,_,run=fixture(tmp_path,monkeypatch,handler)
    async def scenario():
        await agent.start(run);await agent.tasks[run['id']]
        assert len(calls)==1 and not agent.unsettled(run['id'])
        assert store.get(run['id'])['reserved']==0
        assert store.get(run['id'])['status']=='reconciliation_required'
        await agent.client.aclose()
    asyncio.run(scenario())


def test_budget_blocks_next_model_request_and_no_text_only_false_completion(tmp_path,monkeypatch):
    calls=[]
    async def handler(request):calls.append(request);return httpx.Response(200,json=interaction('text-only',status='completed'))
    agent,store,_,_,run=fixture(tmp_path,monkeypatch,handler)
    async def scenario():
        await agent.start(run);await agent.tasks[run['id']]
        assert store.get(run['id'])['status']=='waiting_input'
        store.update(run['id'],spent=100)
        with pytest.raises(Conflict):await agent.reply(run['id'],'続けて')
        assert len(calls)==1
        await agent.client.aclose()
    asyncio.run(scenario())


def test_provider_selection_persists_but_existing_run_stays_on_original(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    class Claude:
        async def close(self):pass
    app=create_app({},tmp_path,managed_client=Claude())
    store=app.state.store;store.set_budget(1000)
    run,_=store.create('local-trial-operator','provider-123','旧依頼',['shift'],100)
    store.update(run['id'],status='waiting_input')
    with TestClient(app) as client:
        headers={'X-Trial-Token':app.state.token}
        assert client.put('/provider',json={'provider':'gemini'},headers=headers).json()['provider_id']=='gemini'
        assert client.put('/provider',json={'provider':'invalid'},headers=headers).status_code==422
        assert client.get('/config',headers=headers).json()['provider_id']=='gemini'
        assert store.get(run['id'])['provider']=='claude'
        assert app.state.managed.for_run(run['id']) is None
    assert Store(tmp_path).provider()=='gemini'
    with pytest.raises(Conflict):store.create('local-trial-operator','provider-123','旧依頼',['shift'],100,provider='gemini')


def test_mcp_image_blocks_convert_without_external_urls():
    assert function_result([{'type':'text','text':'first'},{'type':'text','text':'second'}])=='first\nsecond'
    assert content([{'type':'image','source':{'type':'base64','media_type':'image/png','data':'YWJj'}}])==[{'type':'image','mime_type':'image/png','data':'YWJj'}]
    with pytest.raises(Conflict):content([{'type':'document'}])


def test_all_advertised_provider_tools_are_registered_with_local_mcp(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path, ('school-lunch',))
    async def check():
        registered = {tool.name for tool in await build_mcp(tools).list_tools()}
        for domain in ('school-lunch', 'hospital', 'shift'):
            assert {definition['name'] for definition in tools.definitions([domain])} <= registered
    asyncio.run(check())


def test_provider_receipt_identity_fits_native_question_reference_contract():
    value=command_identity('v1_'+('remote/ID:'*50),'call:1')
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value)
    assert value==command_identity('v1_'+('remote/ID:'*50),'call:1')
    assert value!=command_identity('v1_'+('remote/ID:'*50),'call:2')


@pytest.mark.parametrize('status',[422,409,401])
def test_schema_rejection_returns_to_agent_but_conflicts_and_auth_wait_for_operator(tmp_path,status):
    store,sandbox,tools,run=prepared(tmp_path,('shift',))
    async def native(*args,**kwargs):return httpx.Response(status,json={'detail':'Missing field or conflict'})
    sandbox.request=native
    result=asyncio.run(tools.execute(run['id'],0,'shift_get_staff',{'parameters':{}},'rejected-input'))
    assert not result['ok']
    if status==422:
        assert result['input_validation_failed'] and not result.get('needs_operator_confirmation')
        assert store.get(run['id'])['status']=='running'
    else:
        assert result['needs_operator_confirmation'] and store.get(run['id'])['status']=='waiting_input'


def test_finish_requires_real_tool_receipt_then_stops_without_an_extra_model_turn(tmp_path,monkeypatch):
    calls=[]
    replies=[interaction('read-turn',[('read','shift_get_staff',{'parameters':{}})]),
        interaction('finish-turn',[('finish','finish_work',{'summary':'登録職員を確認','evidence_commands':[command_identity('read-turn','read')],'artifact_ids':[]})])]
    async def handler(request):
        calls.append((request.method,request.url.path))
        if request.method=='GET':return httpx.Response(200,json=interaction(request.url.path.rsplit('/',1)[-1],status='completed'))
        return httpx.Response(200,json=replies.pop(0))
    agent,store,sandbox,_,run=fixture(tmp_path,monkeypatch,handler)
    async def native(*args,**kwargs):return httpx.Response(200,json=[{'id':1,'name':'合成職員'}])
    sandbox.request=native
    async def scenario():
        await agent.start(run);await agent.tasks[run['id']]
        assert store.get(run['id'])['status']=='completed' and store.get(run['id'])['reserved']==0
        assert len([r for r in calls if r[0]=='POST'])==2
        assert any(e['kind']=='completion_evidence' for e in store.events(run['id']))
        await agent.client.aclose()
    asyncio.run(scenario())


def test_gemini_stop_and_restore_keep_cost_and_remove_only_request_changes(tmp_path,monkeypatch):
    import sqlite3
    agent,store,sandbox,_,run=fixture(tmp_path,monkeypatch,lambda _:httpx.Response(500))
    file=sandbox.path(run['id'])/'data/shift/database.sqlite3'
    with sqlite3.connect(file) as db:db.execute('UPDATE quantities SET value=99')
    store.update(run['id'],spent=7)
    async def scenario():
        await agent.stop(run['id']);result=await sandbox.restore(store.get(run['id']))
        store.event(run['id'],'rollback',result)
        store.update(run['id'],status='rolled_back')
        with sqlite3.connect(file) as db:assert db.execute('SELECT value FROM quantities').fetchone()[0]==7
        assert store.get(run['id'])['spent']==7
        assert any(e['kind']=='rollback' for e in store.events(run['id']))
        await agent.client.aclose()
    asyncio.run(scenario())
