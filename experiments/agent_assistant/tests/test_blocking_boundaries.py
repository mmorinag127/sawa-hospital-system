"""Synthetic scheduling/commit boundaries; no native or live business proof."""
import asyncio
from contextvars import ContextVar
import inspect
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from experiments.agent_assistant.app import _complete_request
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.sandbox import run_blocking


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
