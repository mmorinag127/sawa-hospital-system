"""Synthetic local race regressions; no provider calls or live business proof."""
import asyncio
from types import SimpleNamespace

import pytest

from experiments.agent_assistant.conversation import pending_question
from experiments.agent_assistant.gemini import Gemini
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_business_workflow import attach
from experiments.agent_assistant.tests.test_selected_tasks import lunch, setup
from experiments.agent_assistant.tests.test_trial import Event, FakeStream


@pytest.mark.parametrize('status', ['completed', 'stopping', 'stopped', 'rolled_back',
                                   'stop_pending', 'rollback_stop_pending'])
def test_generation_guard_never_changes_terminal_state(tmp_path, status):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    store.update(run['id'], status=status)
    before = store.get(run['id'])
    with pytest.raises(Conflict):
        store.update(run['id'], expected_generation=run['generation'], status='running')
    assert store.get(run['id']) == before


def test_old_generation_cannot_change_a_new_active_generation(tmp_path):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    store.fence(run['id'])
    store.update(run['id'], status='running')
    before = store.get(run['id'])
    with pytest.raises(Conflict):
        store.update(run['id'], expected_generation=run['generation'], status='waiting_input')
    assert store.get(run['id']) == before


@pytest.mark.parametrize('page', [False, True])
@pytest.mark.parametrize('fence', [False, True])
def test_late_layout_and_pagination_answers_preserve_stop(tmp_path, page, fence):
    async def scenario():
        store, native, tools, run = lunch(tmp_path, 'lunch_daily_bundle')
        context = run['task_inputs']
        context['inputs'].pop('layout_id')
        context['inputs'].pop('layout_version')
        context['inputs']['layout_choices'] = [
            {'id': i, 'version': 1, 'name': 'Layout ' + str(i)} for i in range(1, 10)]
        store.update(run['id'], task_inputs=context)
        engine, run = attach(store, native, tools, store.get(run['id']))
        engine.kick = lambda *_: None
        await engine.choose_layout(run)
        question = pending_question(store.events(run['id']))
        waiting = store.get(run['id'])
        if fence:
            store.fence(run['id'])
        else:
            store.update(run['id'], status='stopped')
        before, events = store.get(run['id']), store.events(run['id'])
        answer = question['options'][-1 if page else 0]
        with pytest.raises(Conflict):
            engine.answer_layout(waiting, answer, question['question_id'], answer)
        assert store.get(run['id']) == before
        assert store.events(run['id']) == events
        assert not native.writes
    asyncio.run(scenario())


def test_original_source_notification_cannot_resume_a_stopped_run(tmp_path):
    store, native, tools, run = setup(tmp_path, 'lunch_import', [])
    engine, run = attach(store, native, tools, run)
    store.update(run['id'], status='waiting_input')
    waiting = store.get(run['id'])
    store.fence(run['id'])
    before = store.get(run['id'])
    with pytest.raises(Conflict):
        engine.source_ready(waiting)
    assert store.get(run['id']) == before


@pytest.mark.parametrize('phase', ['source', 'checking'])
def test_advance_cannot_replace_a_stop_during_authorization(tmp_path, phase):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'lunch_import', [])
        engine, run = attach(store, native, tools, run)
        engine.checkpoint(run['id'], phase=phase)
        async def stop_while_authorizing(_):
            store.fence(run['id'])
        tools.authorization_validator = stop_while_authorizing
        with pytest.raises(Conflict):
            await engine.advance(run['id'])
        assert store.get(run['id'])['status'] == 'stopping'
        assert not native.writes
    asyncio.run(scenario())


def test_idle_model_failure_does_not_reset_a_stop(tmp_path):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'lunch_import', [])
        engine, run = attach(store, native, tools, run)
        store.event(run['id'], 'business_model_idle',
                    {'session_id': run['session_id'], 'generation': run['generation']})
        async def late_failure(*_):
            store.fence(run['id'])
            raise RuntimeError('synthetic disconnected provider')
        engine.providers.follow = late_failure
        with pytest.raises(RuntimeError):
            await engine.continue_idle_model(run)
        assert store.get(run['id'])['status'] == 'stopping'
    asyncio.run(scenario())


@pytest.mark.parametrize('provider', ['claude', 'gemini'])
def test_late_tool_question_cannot_reset_stop(tmp_path, monkeypatch, provider):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        async def late_question(*_):
            store.fence(run['id'])
            return {'waiting_input': True, 'question': 'Synthetic question', 'command_id': 'q'}
        module = 'managed' if provider == 'claude' else 'gemini'
        monkeypatch.setattr('experiments.agent_assistant.' + module + '.call_local', late_question)
        manager_type = Managed if provider == 'claude' else Gemini
        client = SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace()))
        manager = manager_type(store, native, tools, None, {}, client=client)
        if provider == 'claude':
            stream = FakeStream([Event(type='agent.custom_tool_use', id='q', name='ask_operator', input={})])
            await manager.consume(run['id'], stream)
        else:
            state = {'interaction': 'synthetic', 'index': 0, 'results': [],
                     'calls': [{'id': 'q', 'name': 'ask_operator', 'arguments': {}}]}
            await manager.consume_gemini(run['id'], run['generation'], state, None)
        assert store.get(run['id'])['status'] == 'stopping'
        assert not any(event['kind'] == 'operator_wait' for event in store.events(run['id']))
    asyncio.run(scenario())


@pytest.mark.parametrize('uncertain', [False, True])
def test_late_created_session_is_stopped_without_resuming_business(tmp_path, monkeypatch, uncertain):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        sent, archived, retrieved = [], [], []
        class Sessions:
            async def create(self, **_):
                store.fence(run['id'])
                store.update(run['id'], status='stopped')
                return SimpleNamespace(id='late-session')
            async def retrieve(self, ident):
                retrieved.append(ident)
                if uncertain:
                    raise OSError('synthetic stop result unknown')
                return SimpleNamespace(status='running' if len(retrieved) == 1 else 'idle',
                                       usage=Event(list_cost={'amount': '17', 'currency': 'USD'}))
            async def send(self, ident, **payload):
                sent.append((ident, payload))
            async def archive(self, ident):
                archived.append(ident)
        sessions = Sessions()
        sessions.events = sessions
        manager = Managed(store, native, tools, None, {},
                          client=SimpleNamespace(beta=SimpleNamespace(sessions=sessions)))
        monkeypatch.setenv('CLAUDE_AGENT_ID', 'synthetic-agent')
        monkeypatch.setenv('CLAUDE_ENVIRONMENT_ID', 'synthetic-environment')
        with pytest.raises(Conflict):
            await manager.start(run)
        after = store.get(run['id'])
        assert after['status'] == 'stopped' and after['generation'] == run['generation'] + 1
        assert after['session_id'] == 'late-session'
        assert not native.writes
        if uncertain:
            assert after['reserved'] == run['reserved']
            assert not sent and not archived
            assert any(event['kind'] == 'late_session_stop_pending' for event in store.events(run['id']))
        else:
            assert sent == [('late-session', {'events': [{'type': 'user.interrupt'}]})]
            assert archived == ['late-session']
            assert after['spent'] == 17 and after['reserved'] == 0
    asyncio.run(scenario())


def test_restart_preserves_stop_intent_generation_and_reserved_cost(tmp_path):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    store.usage(run['id'], {'list_cost': {'amount': '12', 'currency': 'USD'}})
    store.fence(run['id'])
    before = store.get(run['id'])
    store.recover()
    after = store.get(run['id'])
    assert after['status'] == 'stop_pending'
    assert after['generation'] == before['generation'] + 1
    assert (after['spent'], after['reserved']) == (before['spent'], before['reserved'])
    assert store.events(run['id'])[-1]['kind'] == 'gateway_recovery_fence'
