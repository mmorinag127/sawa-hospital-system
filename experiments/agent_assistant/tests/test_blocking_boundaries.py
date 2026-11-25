"""Synthetic scheduling/commit boundaries; no native or live business proof."""
import asyncio
from contextvars import ContextVar
import inspect
from threading import Event, get_ident
from types import SimpleNamespace

import httpx
import pytest

from experiments.agent_assistant.app import _complete_request, create_app
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.sandbox import run_blocking
from experiments.agent_assistant.store import Conflict


def test_blocking_call_keeps_context_without_stopping_the_event_loop():
    async def scenario():
        loop = asyncio.get_running_loop()
        started, release = asyncio.Event(), Event()
        context = ContextVar('synthetic_request')
        context.set('request-a')
        main_thread = get_ident()

        def work():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return get_ident(), context.get()

        task = asyncio.create_task(run_blocking(work))
        try:
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        thread, value = await task
        assert thread != main_thread and value == 'request-a'
    asyncio.run(scenario())


def test_repeated_cancellation_waits_for_the_owned_thread_to_finish():
    async def scenario():
        loop = asyncio.get_running_loop()
        started, release, committed = asyncio.Event(), Event(), Event()

        def work():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            committed.set()

        task = asyncio.create_task(run_blocking(work))
        try:
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done() and not committed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert committed.is_set()
    asyncio.run(scenario())


def test_accepted_request_finishes_commit_and_scheduling_before_cancellation():
    async def scenario():
        loop = asyncio.get_running_loop()
        started, release = asyncio.Event(), Event()
        events = []

        def commit():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            events.append('committed')

        async def operation(identity: str):
            await run_blocking(commit)
            await asyncio.sleep(0)
            events.append(('scheduled', identity))
            return identity

        wrapped = _complete_request(operation)
        assert inspect.signature(wrapped) == inspect.signature(operation)
        task = asyncio.create_task(wrapped('request-a'))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ['committed', ('scheduled', 'request-a')]
    asyncio.run(scenario())


def test_stop_cannot_leave_a_committed_generation_fence_without_cleanup():
    async def scenario():
        loop = asyncio.get_running_loop()
        started, release = asyncio.Event(), Event()
        cleaned = []

        def fence(identity, *, expected_generation):
            assert identity == 'owned' and expected_generation == 7
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return {'status': 'running'}, 8

        managed = Managed.__new__(Managed)
        managed.store = SimpleNamespace(get=lambda _ident: {'status': 'running'}, fence=fence)

        async def cleanup(identity, caller, expected_generation, *, fenced):
            assert identity == 'owned' and expected_generation == 7
            assert fenced == ({'status': 'running'}, 8)
            await asyncio.sleep(0)
            cleaned.append(identity)

        managed._stop = cleanup
        task = asyncio.create_task(managed.stop('owned', expected_generation=7))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not cleaned
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned == ['owned']
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['ready', 'failed', 'stopped'])
@pytest.mark.parametrize('task_id', ['lunch_import', 'lunch_procurement', 'lunch_daily_bundle'])
def test_school_acceptance_is_not_blocked_by_its_owned_copy(tmp_path, monkeypatch, task_id, outcome):
    async def scenario():
        client_stub = SimpleNamespace()
        app = create_app({}, tmp_path, managed_client=client_stub)
        store, sandbox = app.state.store, app.state.sandbox
        store.set_budget(500)
        fax, _ = store.create('local-trial-operator', 'existing-fax', 'fax', ['hospital'], 100, task_id='hospital_pending_faxes')
        store.update(fax['id'], status='reconciliation_required')
        store.event(fax['id'], 'operator_reply', {'message': 'retained answer'})
        original = store.get(fax['id']), store.events(fax['id'])
        started, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        kicked = asyncio.Event()

        def copying(run):
            assert run['domains'] == ['school-lunch']
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            if outcome != 'ready':
                raise Conflict('synthetic preparation failure')

        def unexpected_global_flush(_):
            pytest.fail('an API response must not repersist any historical run')

        async def fake_kick_many(ids, *args):
            if ids:
                assert outcome == 'ready' and task_id != 'lunch_import' and release.is_set()
                kicked.set()

        sandbox.durable_store = SimpleNamespace(persist_files=unexpected_global_flush)
        monkeypatch.setattr(sandbox, 'prepare', copying)
        monkeypatch.setattr(app.state.business, 'kick_many', fake_kick_many)
        monkeypatch.setattr(app.state.tools.flow, 'selection', lambda *_: {'inputs': {'facility_id': 1, 'month': '2027-01'}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', headers={'x-trial-token': app.state.token}) as client:
            try:
                response = await asyncio.wait_for(client.post('/runs', json={'request_key': 'synthetic-school', 'task_id': task_id}), 3)
                assert response.status_code == 201
                await asyncio.wait_for(started.wait(), 3)
                run = response.json()
                assert run['status'] == 'preparing'
                assert app.state.business.projection(run)['next_action'] == 'none'
                assert not kicked.is_set()
                if outcome == 'stopped':
                    previous, generation = store.fence(run['id'])
                    assert previous['status'] == 'preparing'
                    store.finish_stop(run['id'], generation, native_stopped=True, restored=False)
            finally:
                release.set()
            for _ in range(300):
                active = store.get(run['id'])
                if active['status'] != 'preparing' or kicked.is_set():
                    break
                await asyncio.sleep(.01)
            if outcome == 'ready' and task_id != 'lunch_import':
                assert kicked.is_set()
            else:
                assert active['status'] == {'ready': 'waiting_input', 'failed': 'failed', 'stopped': 'stopped'}[outcome]
                assert not kicked.is_set()
            if outcome != 'ready':
                assert active['reserved'] == 0
            assert (store.get(fax['id']), store.events(fax['id'])) == original

    asyncio.run(scenario())
