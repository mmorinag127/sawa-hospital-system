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
        assert await engine.advance(run['id']) is False
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


def test_stop_snapshot_and_generation_are_one_transaction(tmp_path):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    previous, generation = store.fence(run['id'], expected_generation=run['generation'])
    assert previous == run
    assert generation == run['generation'] + 1
    assert store.fence(run['id'], expected_generation=run['generation']) is None


@pytest.mark.parametrize('action', ['status', 'reservation', 'rollback', 'no_model'])
def test_old_stop_or_completion_cannot_change_new_generation(tmp_path, action):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    _, old_stop = store.fence(run['id'])
    store.recover()
    store.update(run['id'], status='completed' if action == 'no_model' else 'running')
    before = store.get(run['id'])
    with pytest.raises(Conflict):
        if action == 'status':
            store.update(run['id'], expected_stop_generation=old_stop, status='stopped')
        elif action == 'reservation':
            store.update(run['id'], expected_stop_generation=old_stop, reserved=0)
        elif action == 'rollback':
            store.finish_rollback(run['id'], old_stop, {'restored': True})
        else:
            store.release_unstarted_model(run['id'], old_stop)
    assert store.get(run['id']) == before


def test_stale_usage_records_cost_without_refunding_new_reservation(tmp_path):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    _, old_stop = store.fence(run['id'])
    store.recover()
    store.update(run['id'], status='running', session_id='new-session')
    store.usage(run['id'], {'list_cost': {'amount': '17', 'currency': 'USD'}},
                terminal=True, terminal_generation=old_stop, terminal_session='old-session')
    current = store.get(run['id'])
    assert current['spent'] == 17
    assert current['reserved'] == 83
    assert current['status'] == 'running' and current['session_id'] == 'new-session'


@pytest.mark.parametrize('mismatch', ['generation', 'session', 'status'])
def test_each_terminal_usage_guard_independently_prevents_refund(tmp_path, mismatch):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    _, generation = store.fence(run['id'])
    status = 'running' if mismatch == 'status' else 'stopped'
    store.update(run['id'], status=status, session_id='current-session')
    store.usage(run['id'], {'list_cost': {'amount': '17', 'currency': 'USD'}}, terminal=True,
                terminal_generation=generation - 1 if mismatch == 'generation' else generation,
                terminal_session='old-session' if mismatch == 'session' else 'current-session',
                terminal_statuses=('stopped', 'rolled_back'))
    current = store.get(run['id'])
    assert current['reserved'] == 83 and current['spent'] == 17
    assert current['status'] == status and current['session_id'] == 'current-session'


@pytest.mark.parametrize('remote_pending', [False, True])
def test_rollback_receipt_preserves_remote_stop_uncertainty(tmp_path, remote_pending):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    _, generation = store.fence(run['id'])
    store.update(run['id'], status='stop_pending' if remote_pending else 'stopped')
    receipt = {'restored': True, 'scope': 'this_request_sandbox'}
    store.finish_rollback(run['id'], generation, receipt)
    assert store.get(run['id'])['status'] == ('rollback_stop_pending' if remote_pending else 'rolled_back')
    assert store.events(run['id'])[-1]['payload'] == receipt
    assert store.get(run['id'])['reserved'] == 100


def test_late_gemini_receipt_does_not_replace_new_session(tmp_path):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        manager = Gemini(store, native, tools, None, {})
        async def response(*args, **kwargs):
            store.fence(run['id'])
            store.update(run['id'], status='running', session_id='new-session')
            return {'id': 'old-interaction'}
        manager.request = response
        result = await manager.dispatch_receipt(run['id'], 'old-dispatch', {}, run['generation'])
        assert result['id'] == 'old-interaction'
        assert store.get(run['id'])['session_id'] == 'new-session'
        assert store.events(run['id'])[-1]['kind'] == 'late_gemini_response'
        assert store.get(run['id'])['reserved'] == 100
    asyncio.run(scenario())


def test_claude_old_stop_only_stops_its_captured_session(tmp_path):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        store.update(run['id'], session_id='old-session')
        seen = []
        async def native_stop(ident, *, expected_generation=None):
            assert expected_generation == run['generation'] + 1
            store.recover()
            store.update(ident, status='running', session_id='new-session')
            return True
        native.stop = native_stop
        class Sessions:
            async def retrieve(self, ident):
                seen.append(ident)
                return SimpleNamespace(status='idle', usage=Event(list_cost={'amount': '17', 'currency': 'USD'}))
            async def archive(self, ident):
                seen.append(ident)
        manager = Managed(store, native, tools, None, {},
                          client=SimpleNamespace(beta=SimpleNamespace(sessions=Sessions())))
        with pytest.raises(Conflict):
            await manager.stop(run['id'])
        current = store.get(run['id'])
        assert seen and set(seen) == {'old-session'}
        assert current['status'] == 'running' and current['session_id'] == 'new-session'
        assert current['reserved'] == 83
    asyncio.run(scenario())


@pytest.mark.parametrize('provider', [Managed, Gemini])
@pytest.mark.parametrize('outcome', [False, None, 'exception'])
@pytest.mark.parametrize('remote_unknown', [False, True])
def test_unconfirmed_native_stop_still_stops_remote_and_retains_reservation(tmp_path, provider, outcome, remote_unknown):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        store.update(run['id'], session_id='old-session')
        async def unconfirmed(ident, *, expected_generation=None):
            assert expected_generation == run['generation'] + 1
            if outcome == 'exception':
                raise OSError('synthetic native stop failure')
            return outcome
        native.stop = unconfirmed
        manager = provider(store, native, tools, None, {})
        stopped = []
        if provider is Managed:
            retrieved = []
            class Sessions:
                async def retrieve(self, ident):
                    if remote_unknown:
                        raise OSError('synthetic remote stop unknown')
                    retrieved.append(ident)
                    return SimpleNamespace(status='running' if len(retrieved) == 1 else 'idle',
                                           usage=Event(list_cost={'amount': '17', 'currency': 'USD'}))
                async def send(self, ident, **kwargs):
                    stopped.append(ident)
                async def archive(self, ident):
                    pass
            sessions = Sessions()
            sessions.events = sessions
            manager.client = SimpleNamespace(beta=SimpleNamespace(sessions=sessions))
        else:
            manager.pin = lambda ident: {'model': 'synthetic', 'input_rate': '1', 'output_rate': '1'}
            store.event(run['id'], 'gemini_dispatch', {'id': 'dispatch'})
            store.event(run['id'], 'gemini_response', {'id': 'old-session', 'dispatch': 'dispatch'})
            async def request(method, path='', body=None):
                if remote_unknown:
                    raise OSError('synthetic remote stop unknown')
                if path.endswith('/cancel'):
                    stopped.append('old-session')
                    return {}
                return {'id': 'old-session', 'status': 'completed' if stopped else 'in_progress',
                        'usage': {'total_input_tokens': 10, 'total_output_tokens': 1, 'total_thought_tokens': 0}}
            manager.request = request
        await manager.stop(run['id'])
        current = store.get(run['id'])
        assert stopped == ([] if remote_unknown else ['old-session'])
        assert current['status'] == 'stop_pending'
        assert current['reserved'] > 0
        assert current['reserved'] == run['reserved'] - current['spent']
        notices = [event['payload']['message'] for event in store.events(run['id']) if event['kind'] == 'notice']
        assert any('未確定' in message or '未確認' in message for message in notices)
        assert not any('業務書込みは停止しました' in message for message in notices)
    asyncio.run(scenario())


def test_cleanup_survives_repeated_caller_cancellation():
    from experiments.agent_assistant.sandbox import settle_process_task
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        completed = []
        async def cleanup():
            started.set()
            await release.wait()
            completed.append(True)
            return 'cleaned'
        async def caller():
            return await settle_process_task(asyncio.create_task(cleanup()))
        task = asyncio.create_task(caller())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not completed
        release.set()
        result, cancelled = await task
        assert result == 'cleaned' and isinstance(cancelled, asyncio.CancelledError)
        assert completed == [True]
    asyncio.run(scenario())


def test_gone_parent_with_live_process_group_is_not_guessed_owned(monkeypatch):
    from experiments.agent_assistant import sandbox
    signals = []
    def gone(pid):
        raise sandbox.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(sandbox.psutil, 'Process', gone)
    monkeypatch.setattr(sandbox.os, 'killpg', lambda pid, signal: signals.append((pid, signal)))
    with pytest.raises(Conflict, match='所有権'):
        sandbox.require_process_group_owner(1234, 'a' * 32)
    assert signals == [(1234, 0)]


def test_foreign_reused_pid_never_receives_termination(monkeypatch):
    from experiments.agent_assistant import sandbox
    signals = []
    monkeypatch.setattr(sandbox.psutil, 'Process', lambda pid: SimpleNamespace(
        cmdline=lambda: ['unrelated-user-process'], create_time=lambda: 12.0))
    monkeypatch.setattr(sandbox.os, 'killpg', lambda *args: signals.append(args))
    with pytest.raises(Conflict, match='所有権'):
        sandbox.require_process_group_owner(1234, 'a' * 32, 12.0)
    assert signals == []


def test_gemini_stop_includes_response_arriving_while_start_is_cancelled(tmp_path):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        manager = Gemini(store, native, tools, None, {})
        manager.pin = lambda ident: {'model': 'synthetic', 'input_rate': '1', 'output_rate': '1'}
        ready = asyncio.Event()
        cancelled = []
        async def request(method, path='', body=None):
            if method == 'POST' and not path:
                return {'id': 'late-interaction'}
            if path.endswith('/cancel'):
                cancelled.append(path)
                return {}
            return {'id': 'late-interaction', 'status': 'completed' if cancelled else 'in_progress',
                    'usage': {'total_input_tokens': 10, 'total_output_tokens': 1, 'total_thought_tokens': 0}}
        manager.request = request
        store.event(run['id'], 'gemini_dispatch', {'id': 'old-dispatch', 'generation': run['generation']})
        async def starting():
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await manager.dispatch_receipt(run['id'], 'old-dispatch', {}, run['generation'])
                raise
        manager.starting[run['id']] = asyncio.create_task(starting())
        await ready.wait()
        await manager.stop(run['id'])
        assert cancelled == ['/late-interaction/cancel']
        assert store.get(run['id'])['status'] == 'stopped'
        assert store.get(run['id'])['reserved'] == 0
        assert store.get(run['id'])['spent'] == 1
    asyncio.run(scenario())


def test_old_gemini_stop_never_cancels_a_new_generation_interaction(tmp_path):
    async def scenario():
        store, native, tools, run = setup(tmp_path, 'shift_next_month', [])
        manager = Gemini(store, native, tools, None, {})
        manager.pin = lambda ident: {'model': 'synthetic', 'input_rate': '1', 'output_rate': '1'}
        store.event(run['id'], 'gemini_dispatch', {'id': 'old-dispatch'})
        store.event(run['id'], 'gemini_response', {'id': 'old-interaction', 'dispatch': 'old-dispatch'})
        cancelled = []
        async def stop_native(ident, *, expected_generation=None):
            store.recover()
            store.update(ident, status='running', session_id='new-interaction')
            store.event(ident, 'gemini_dispatch', {'id': 'new-dispatch'})
            store.event(ident, 'gemini_response', {'id': 'new-interaction', 'dispatch': 'new-dispatch'})
            return True
        native.stop = stop_native
        async def request(method, path='', body=None):
            if path.endswith('/cancel'):
                cancelled.append(path)
                return {}
            ident = path[1:]
            return {'id': ident, 'status': 'completed' if ident == 'old-interaction' and cancelled else 'in_progress',
                    'usage': {'total_input_tokens': 10, 'total_output_tokens': 1, 'total_thought_tokens': 0}}
        manager.request = request
        with pytest.raises(Conflict):
            await manager.stop(run['id'])
        assert cancelled == ['/old-interaction/cancel']
        current = store.get(run['id'])
        assert current['status'] == 'running' and current['session_id'] == 'new-interaction'
        assert current['reserved'] > 0
    asyncio.run(scenario())
