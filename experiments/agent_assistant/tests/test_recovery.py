"""Restart proofs use isolated copies and HTTP doubles; no model spend or live DB."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from experiments.agent_assistant.recovery import Recovery
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_gemini import fixture, interaction
from experiments.agent_assistant.gemini import command_identity
from experiments.agent_assistant.providers import Providers
from experiments.agent_assistant.mcp_server import build_mcp
from experiments.agent_assistant.tests.test_trial import prepared
from experiments.agent_assistant.conversation import pending_question
from experiments.agent_assistant.tools import Arguments


def setup(tmp_path, monkeypatch, response):
    requests = []
    async def handler(request):
        requests.append(request.method)
        assert request.method == 'GET'
        return httpx.Response(200, json=response)
    agent, store, sandbox, tools, run = fixture(tmp_path, monkeypatch, handler)
    with store.tx() as db:
        db.execute("UPDATE runs SET provider='gemini',session_id='turn',status='reconciliation_required' WHERE id=?", (run['id'],))
    store.event(run['id'], 'gemini_dispatch', {'id': 'dispatch'})
    store.event(run['id'], 'gemini_response', {'id': 'turn', 'dispatch': 'dispatch'})
    recovery = Recovery(SimpleNamespace(for_run=lambda _: agent, store=store))
    return recovery, agent, store, sandbox, tools, run, requests


def test_restart_restores_same_question_without_model_post_or_native_write(tmp_path, monkeypatch):
    args = {'question': '確認しますか？', 'options': ['はい', 'いいえ']}
    response = interaction('turn', [('question', 'ask_operator', args)])
    recovery, agent, store, _, _, run, requests = setup(tmp_path, monkeypatch, response)
    command = command_identity('turn', 'question')
    store.command(run['id'], command, {'name': 'ask_operator', 'arguments': args})
    result = {'waiting_input': True, 'question': args['question'], 'options': args['options'], 'command_id': command}
    store.finish_command(run['id'], command, result)
    store.event(run['id'], 'operator_wait', {'command_id': command, 'result': result})
    agent.checkpoint(run['id'], {'interaction': 'turn', 'calls': response['steps'], 'results': [], 'index': 0, 'pending': command})
    before = store.get(run['id'])
    async def scenario():
        recovered = await recovery.resume(run['id'])
        assert recovered['status'] == 'waiting_input'
        assert recovered['spent'] + recovered['reserved'] == before['spent'] + before['reserved']
        assert recovered['generation'] == before['generation']
        assert requests == ['GET']
        assert pending_question(store.events(run['id']))['question_id'] == command
        with pytest.raises(Conflict): await recovery.resume(run['id'])
        await agent.client.aclose()
    asyncio.run(scenario())


def test_saved_business_answer_is_resumed_after_history_proves_no_remote_answer(tmp_path, monkeypatch):
    args = {'question': '登録内容で作成しますか？', 'options': ['登録内容で進める']}
    response = interaction('turn', [('question', 'ask_operator', args)])
    recovery, agent, store, _, _, run, requests = setup(tmp_path, monkeypatch, response)
    context = {'business_workflow': {'version': 1, 'phase': 'checking'}}
    with store.tx() as db:
        import json
        db.execute("UPDATE runs SET task_id='shift_next_month',task_inputs=? WHERE id=?", (json.dumps(context), run['id']))
    command = command_identity('turn', 'question')
    store.command(run['id'], command, {'name': 'ask_operator', 'arguments': args})
    result = {'waiting_input': True, 'question': args['question'], 'options': args['options'], 'command_id': command}
    store.finish_command(run['id'], command, result)
    store.event(run['id'], 'operator_wait', {'command_id': command, 'result': result})
    store.event(run['id'], 'operator_reply', {'question_id': command, 'message': '登録内容で進める', 'option': '登録内容で進める'})
    agent.checkpoint(run['id'], {'interaction': 'turn', 'calls': response['steps'], 'results': [], 'index': 0, 'pending': command})
    sent = []
    async def follow(ident, event):
        sent.append(event)
    agent.follow = follow
    async def scenario():
        recovered = await recovery.resume(run['id'])
        assert recovered['status'] == 'running'
        assert requests == ['GET']
        assert len(sent) == 1 and sent[0]['custom_tool_use_id'] == command
        assert '登録内容で進める' in sent[0]['content'][0]['text']
        assert len([event for event in store.events(run['id']) if event['kind'] == 'operator_reply']) == 1
        assert pending_question(store.events(run['id'])) is None
        await agent.client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['unknown_dispatch', 'unfinished_mutation', 'stopped', 'missing_copy', 'authorization'])
def test_restart_never_replays_unknown_mutations_or_bypasses_stop(tmp_path, monkeypatch, failure):
    recovery, agent, store, sandbox, tools, run, requests = setup(tmp_path, monkeypatch, interaction('turn'))
    if failure == 'unknown_dispatch': store.event(run['id'], 'gemini_dispatch', {'id': 'unknown'})
    if failure == 'unfinished_mutation': store.command(run['id'], 'mutation', {'name': 'shift_solve', 'arguments': {}})
    if failure == 'stopped': store.update(run['id'], status='stopped')
    if failure == 'missing_copy': (sandbox.path(run['id']) / 'data/shift/database.sqlite3').unlink()
    if failure == 'authorization':
        async def deny(_): raise Conflict('権限なし')
        tools.authorization_validator = deny
    before = store.get(run['id'])
    async def scenario():
        with pytest.raises(Conflict): await recovery.resume(run['id'])
        after = store.get(run['id'])
        assert (after['status'], after['reserved'], after['generation']) == (before['status'], before['reserved'], before['generation'])
        assert not requests
        await agent.client.aclose()
    asyncio.run(scenario())


def test_restart_reuses_done_question_receipt_if_checkpoint_was_not_saved(tmp_path, monkeypatch):
    args = {'question': 'どちらですか？', 'options': ['A', 'B']}
    response = interaction('turn', [('question', 'ask_operator', args)])
    recovery, agent, store, _, _, run, requests = setup(tmp_path, monkeypatch, response)
    command = command_identity('turn', 'question')
    store.command(run['id'], command, {'name': 'ask_operator', 'arguments': args})
    store.finish_command(run['id'], command, {'waiting_input': True, 'question': args['question'], 'options': args['options'], 'command_id': command})
    async def scenario():
        await recovery.resume(run['id'])
        await agent.tasks[run['id']]
        assert store.get(run['id'])['status'] == 'waiting_input'
        assert agent.state(run['id'])['pending'] == command
        assert pending_question(store.events(run['id']))['question_id'] == command
        assert requests == ['GET']
        await agent.client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('saved_wait', [False, True])
def test_claude_history_recovers_exact_question_and_never_resends_model_input(tmp_path, saved_wait):
    store, sandbox, tools, run = prepared(tmp_path, ('shift',))
    args = {'question': '確認しますか？', 'options': ['はい', 'いいえ']}
    payload = {'id': 'call', 'type': 'agent.custom_tool_use', 'name': 'ask_operator', 'input': args}
    class Event:
        id = 'call'
        def model_dump(self, **_): return payload
    class Events:
        sends = 0
        def list(self, *_args, **_kwargs):
            async def values(): yield Event()
            return values()
        async def stream(self, *_): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def __aiter__(self):
            if False: yield None
        async def send(self, *_args, **_kwargs):
            self.sends += 1
            raise AssertionError('question must remain pending')
    events = Events()
    async def retrieve(_):
        usage=SimpleNamespace(model_dump=lambda **_: {'list_cost': {'currency': 'USD', 'amount': '0'}})
        return SimpleNamespace(id='session',status='idle',usage=usage)
    api = SimpleNamespace(events=events, retrieve=retrieve)
    provider = Providers(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=api)))
    async def boot(_): pass
    sandbox.start = boot
    store.update(run['id'], status='reconciliation_required', session_id='session')
    store.event(run['id'], payload['type'], payload, 'call')
    store.command(run['id'], 'call', {'name': 'ask_operator', 'arguments': args})
    result = {'waiting_input': True, 'question': args['question'], 'options': args['options'], 'command_id': 'call'}
    store.finish_command(run['id'], 'call', result)
    if saved_wait: store.event(run['id'], 'operator_wait', {'command_id': 'call', 'result': result})
    async def scenario():
        await provider.resume(run['id'])
        if not saved_wait: await provider.tasks[run['id']]
        assert store.get(run['id'])['status'] == 'waiting_input'
        assert store.get(run['id'])['reserved'] == 100 and events.sends == 0
        assert len([e for e in store.events(run['id']) if e['kind'] == 'operator_wait']) == 1
        assert pending_question(store.events(run['id']))['question_id'] == 'call'
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['completed', 'requires_action'])
def test_completed_remote_without_pending_call_stays_blocked(tmp_path, monkeypatch, status):
    recovery, agent, store, _, _, run, _ = setup(tmp_path, monkeypatch, interaction('turn', status=status))
    async def scenario():
        with pytest.raises(Conflict, match='再開対象'): await recovery.resume(run['id'])
        assert store.get(run['id'])['status'] == 'reconciliation_required'
        assert store.get(run['id'])['spent'] + store.get(run['id'])['reserved'] == 100
        await agent.client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['wait_for_task_decision', 'wait_for_native_job'])
def test_restart_rebinds_only_read_wait_and_then_asks_next_question(tmp_path, monkeypatch, name):
    raw = {} if name == 'wait_for_task_decision' else {'operation': 'shift_get_run', 'parameters': Arguments(ids={'run_id': 'known-run'}).model_dump()}
    calls = [('wait', name, raw), ('question', 'ask_operator', {'question': '次に進めますか？'})]
    recovery, agent, store, _, tools, run, requests = setup(tmp_path, monkeypatch, interaction('turn', calls))
    command = command_identity('turn', 'wait')
    store.command(run['id'], command, {'name': name, 'arguments': raw})
    tools.flow.active = lambda _: True
    tools.flow.review = lambda _: {'id': 'review', 'state': 'approved', 'decision': {'approve': True}}
    reads = []
    async def scope(*_args, **_kwargs): pass
    async def read(_run, operation, parameters):
        reads.append((operation, parameters))
        return {'status': 'completed', 'id': 'known-run'}
    tools.flow.scoped, tools.flow.read = scope, read
    async def scenario():
        await recovery.resume(run['id'])
        await agent.tasks[run['id']]
        assert store.get(run['id'])['status'] == 'waiting_input'
        assert pending_question(store.events(run['id']))['question_id'] == command_identity('turn', 'question')
        with store.read() as db:
            assert db.execute('SELECT status FROM commands WHERE run_id=? AND id=?', (run['id'], command)).fetchone()[0] == 'done'
        assert requests == ['GET']
        assert reads == ([] if name == 'wait_for_task_decision' else [('shift_get_run', raw['parameters'])])
        assert store.get(run['id'])['spent'] + store.get(run['id'])['reserved'] == 100
        before = len(reads)
        store.fence(run['id'])
        with pytest.raises(Conflict): await tools.wait_task(run['id'], 0, name, raw, command)
        assert len(reads) == before
        await agent.client.aclose()
    asyncio.run(scenario())


def test_usage_reconciliation_cannot_start_work_after_cap_is_reached(tmp_path, monkeypatch):
    response=interaction('turn',[('question','ask_operator',{'question':'確認？'})])
    response['usage']['total_input_tokens']=100000000
    recovery,agent,store,_,_,run,requests=setup(tmp_path,monkeypatch,response)
    async def scenario():
        with pytest.raises(Conflict,match='上限'):await recovery.resume(run['id'])
        assert store.get(run['id'])['status']=='reconciliation_required'
        assert run['id'] not in agent.tasks and requests==['GET']
        assert store.get(run['id'])['spent']>=100
        await agent.client.aclose()
    asyncio.run(scenario())


def test_resume_api_requires_trial_auth_and_refreshes_run_authorization(tmp_path,monkeypatch):
    from experiments.agent_assistant.app import create_app
    monkeypatch.setenv('SAWA_TRIAL_OPERATOR_ACCOUNT','user')
    args={'question':'続けますか？','options':[]}
    response=interaction('turn',[('question','ask_operator',args)])
    _,agent,store,sandbox,_,run,_=setup(tmp_path,monkeypatch,response)
    app=create_app({'domains':sandbox.configs},sandbox.root,gemini_client=agent.client)
    async def boot(_):pass
    app.state.sandbox.start=boot
    authorizations=[]
    async def authorize(current):
        authorizations.append(app.state.tools.authorizations.get(current['id']))
    app.state.tools.authorization_validator=authorize
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            path='/runs/'+run['id']+'/resume'
            assert (await client.post(path)).status_code==401
            headers={'x-trial-token':app.state.token,'Authorization':'Bearer refreshed-login'}
            assert (await client.post(path,headers=headers)).status_code==200
            await app.state.managed.tasks[run['id']]
            detail=(await client.get('/runs/'+run['id'],headers=headers)).json()
            assert detail['status']=='waiting_input' and detail['pending_question']['question_id']==command_identity('turn','question')
            assert authorizations and set(authorizations)=={'Bearer refreshed-login'}
            assert (await client.post(path,headers=headers)).status_code==409
        await agent.client.aclose()
    asyncio.run(scenario())
