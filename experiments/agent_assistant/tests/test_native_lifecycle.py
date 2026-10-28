"""Owned lifecycle races with synthetic processes; never signals real PIDs."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from experiments.agent_assistant import sandbox as module
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_selected_tasks import setup


def owned(tmp_path):
    store, _, _, run = setup(tmp_path, 'shift_next_month', [])
    sandbox = module.Sandbox(tmp_path / 'native', {}, run_store=store)
    sandbox.path(run['id']).mkdir(parents=True)
    return store, sandbox, run


@pytest.mark.parametrize('marker_failure', [False, True])
def test_launch_creation_is_owned_until_cancel_or_registration_cleanup(tmp_path, monkeypatch, marker_failure):
    async def scenario():
        store, sandbox, run = owned(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()
        process = SimpleNamespace(pid=123456, returncode=None)
        async def wait():
            process.returncode = 0
            return 0
        process.wait = wait
        async def spawn(*args, **kwargs):
            started.set()
            await release.wait()
            return process
        terminated = []
        async def terminate(pid, wait, owner):
            assert owner()
            terminated.append(pid)
            await wait()
        monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
        monkeypatch.setattr(module.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 12.0))
        monkeypatch.setattr(module, 'require_process_group_owner', lambda *args: True)
        monkeypatch.setattr(module, 'terminate_process_group', terminate)
        if marker_failure:
            replace = module.os.replace
            def fail_marker(source, target):
                if str(target).endswith('.process.json'):
                    raise OSError('synthetic marker write failure')
                return replace(source, target)
            monkeypatch.setattr(module.os, 'replace', fail_marker)
        launch = asyncio.create_task(sandbox.launch(run, 'shift', 'synthetic-python', tmp_path,
                                                    {'SAWA_TRIAL_TOKEN': 'synthetic'}))
        await started.wait()
        if not marker_failure:
            launch.cancel()
            await asyncio.sleep(0)
            launch.cancel()
            _, generation = store.fence(run['id'])
            stop = asyncio.create_task(sandbox.stop(run['id'], expected_generation=generation))
            await asyncio.sleep(0)
            assert not stop.done()
        release.set()
        result = (await asyncio.gather(launch, return_exceptions=True))[0]
        assert isinstance(result, OSError if marker_failure else asyncio.CancelledError)
        if not marker_failure:
            assert await stop is True
        assert terminated == [process.pid]
        assert not sandbox.children and not sandbox.logs and not sandbox.urls
        assert not list(sandbox.path(run['id']).glob('*.process.*'))
    asyncio.run(scenario())


def test_stop_checks_generation_after_waiting_for_native_lock(tmp_path):
    async def scenario():
        store, sandbox, run = owned(tmp_path)
        called = []
        async def stop(ident):
            called.append(ident)
        sandbox._stop = stop
        async with sandbox.lock:
            task = asyncio.create_task(sandbox.stop(run['id'], expected_generation=run['generation']))
            await asyncio.sleep(0)
            store.fence(run['id'])
            store.update(run['id'], status='running')
        assert await task is False
        assert not called
    asyncio.run(scenario())


@pytest.mark.parametrize('change_during_stop', [False, True])
def test_restore_never_swaps_directories_after_generation_change(tmp_path, change_during_stop):
    async def scenario():
        store, sandbox, run = owned(tmp_path)
        store.update(run['id'], status='stopped')
        root = sandbox.path(run['id'])
        for name, value in [('before', 'original'), ('data', 'operator-edit')]:
            (root / name).mkdir()
            (root / name / 'value').write_text(value)
        calls = []
        async def stop(ident):
            calls.append(ident)
            if change_during_stop:
                store.fence(ident)
        sandbox._stop = stop
        if change_during_stop:
            task = asyncio.create_task(sandbox.restore(store.get(run['id'])))
        else:
            async with sandbox.lock:
                task = asyncio.create_task(sandbox.restore(store.get(run['id'])))
                await asyncio.sleep(0)
                store.fence(run['id'])
        with pytest.raises(Conflict):
            await task
        assert len(calls) == int(change_during_stop)
        assert (root / 'data/value').read_text() == 'operator-edit'
        assert (root / 'before/value').read_text() == 'original'
        assert not list(root.glob('discarded-*'))
    asyncio.run(scenario())


@pytest.mark.parametrize('uncertain', [False, True])
def test_restart_does_not_launch_until_orphan_cleanup_finishes(tmp_path, uncertain):
    async def scenario():
        _, sandbox, run = owned(tmp_path)
        (sandbox.path(run['id']) / 'shift.process.json').write_text(json.dumps({'pid': 123456, 'created': 12}))
        started, release = asyncio.Event(), asyncio.Event()
        cleaned, launches = [], []
        async def stop(ident):
            started.set()
            await release.wait()
            if uncertain:
                raise Conflict('synthetic unknown ownership')
            cleaned.append(ident)
        async def launch(*args):
            launches.append(True)
        sandbox._stop, sandbox.launch = stop, launch
        task = asyncio.create_task(sandbox.start(run))
        await started.wait()
        if not uncertain:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(Conflict if uncertain else asyncio.CancelledError):
            await task
        assert not launches
        assert cleaned == ([] if uncertain else [run['id']])
    asyncio.run(scenario())


def test_stop_attempts_all_groups_and_retains_uncertain_ownership(tmp_path, monkeypatch):
    async def scenario():
        _, sandbox, run = owned(tmp_path)
        attempted = []
        for index, domain in enumerate(['shift', 'school-lunch'], 1):
            key = run['id'], domain
            async def wait():
                return 0
            sandbox.children[key] = SimpleNamespace(pid=index, wait=wait)
            sandbox.logs[key] = open(sandbox.path(run['id']) / (domain + '.log'), 'ab')
            (sandbox.path(run['id']) / (domain + '.process.json')).write_text(json.dumps({'pid': index, 'created': 12}))
        async def terminate(pid, wait, owner):
            attempted.append(pid)
            if pid == 1:
                raise Conflict('synthetic unknown group')
            await wait()
        monkeypatch.setattr(module, 'terminate_process_group', terminate)
        try:
            with pytest.raises(Conflict):
                await sandbox.stop(run['id'])
            assert sorted(attempted) == [1, 2]
            assert len(sandbox.children) == 2
            assert len(list(sandbox.path(run['id']).glob('*.process.json'))) == 2
        finally:
            for log in sandbox.logs.values():
                log.close()
    asyncio.run(scenario())


def test_broken_marker_does_not_skip_other_groups(tmp_path, monkeypatch):
    async def scenario():
        _, sandbox, run = owned(tmp_path)
        root = sandbox.path(run['id'])
        (root / 'broken.process.json').write_text('invalid-json')
        (root / 'owned.process.json').write_text(json.dumps({'pid': 123456, 'created': 12}))
        process = SimpleNamespace(pid=123456, wait=lambda timeout: 0)
        monkeypatch.setattr(module.psutil, 'Process', lambda pid: process)
        attempted = []
        async def terminate(pid, wait, owner):
            attempted.append(pid)
        monkeypatch.setattr(module, 'terminate_process_group', terminate)
        with pytest.raises(json.JSONDecodeError):
            await sandbox.stop(run['id'])
        assert attempted == [123456]
        assert (root / 'broken.process.json').exists()
        assert not (root / 'owned.process.json').exists()
    asyncio.run(scenario())


def test_process_group_escalates_to_kill_and_waits_for_reaping(monkeypatch):
    async def scenario():
        terminated = asyncio.Event()
        signals, reaped = [], []
        async def wait():
            await terminated.wait()
            reaped.append(True)
        def killpg(pid, action):
            if action == 0:
                if terminated.is_set():
                    raise ProcessLookupError
            else:
                signals.append(action)
                if action == module.signal.SIGKILL:
                    terminated.set()
        monkeypatch.setattr(module.os, 'killpg', killpg)
        loop = asyncio.get_running_loop()
        ticks = [loop.time()]
        real_sleep = asyncio.sleep
        async def advance_and_yield(delay):
            ticks[0] += 9
            await real_sleep(0)
        monkeypatch.setattr(loop, 'time', lambda: ticks[0])
        monkeypatch.setattr(module.asyncio, 'sleep', advance_and_yield)
        await module.terminate_process_group(123456, wait, lambda: True)
        assert signals == [module.signal.SIGTERM, module.signal.SIGKILL]
        assert reaped == [True]
    asyncio.run(scenario())


@pytest.mark.parametrize('mismatch', ['created', 'pgid'])
def test_owner_timestamp_and_group_checks_are_independent(monkeypatch, mismatch):
    run_id, pid = 'a' * 32, 123456
    monkeypatch.setattr(module.psutil, 'Process', lambda ident: SimpleNamespace(
        cmdline=lambda: [str(module.Path(module.__file__).with_name('domain_host.py')), run_id],
        create_time=lambda: 13 if mismatch == 'created' else 12))
    monkeypatch.setattr(module.os, 'getpgid', lambda ident: pid + 1 if mismatch == 'pgid' else pid)
    signals = []
    monkeypatch.setattr(module.os, 'killpg', lambda *args: signals.append(args))
    with pytest.raises(Conflict, match='所有権'):
        module.require_process_group_owner(pid, run_id, 12)
    assert not signals


def test_os_denied_owner_inspection_never_signals_unknown_process(monkeypatch):
    def denied(_):
        raise module.psutil.AccessDenied(123456)
    monkeypatch.setattr(module.psutil, 'Process', denied)
    signals = []
    monkeypatch.setattr(module.os, 'killpg', lambda *args: signals.append(args))
    with pytest.raises(Conflict, match='OS'):
        module.require_process_group_owner(123456, 'a' * 32, 12)
    assert signals == [(123456, 0)]


def test_disappeared_group_after_denied_inspection_is_not_signalled(monkeypatch):
    def denied(_):
        raise module.psutil.AccessDenied(123456)
    def gone(pid, action):
        assert (pid, action) == (123456, 0)
        raise ProcessLookupError
    monkeypatch.setattr(module.psutil, 'Process', denied)
    monkeypatch.setattr(module.os, 'killpg', gone)
    assert module.require_process_group_owner(123456, 'a' * 32, 12) is False


@pytest.mark.parametrize('denied_action', [0, module.signal.SIGTERM])
def test_os_denied_cleanup_keeps_markers_and_native_handles(tmp_path, monkeypatch, denied_action):
    async def scenario():
        _, sandbox, run = owned(tmp_path)
        key, pid = (run['id'], 'shift'), 123456
        async def wait():
            await asyncio.Event().wait()
        sandbox.children[key] = SimpleNamespace(pid=pid, wait=wait)
        log = open(sandbox.path(run['id']) / 'shift.log', 'ab')
        sandbox.logs[key] = log
        marker = sandbox.path(run['id']) / 'shift.process.json'
        marker.write_text(json.dumps({'pid': pid, 'created': 12}))
        before = marker.read_bytes()
        signals = []
        def killpg(ident, action):
            assert ident == pid
            signals.append(action)
            if action == denied_action:
                raise PermissionError('synthetic OS restriction')
        monkeypatch.setattr(module.os, 'killpg', killpg)
        monkeypatch.setattr(module, 'require_process_group_owner', lambda *args: True)
        try:
            with pytest.raises(Conflict, match='OS'):
                await sandbox.stop(run['id'])
            assert key in sandbox.children
            assert marker.read_bytes() == before
            assert sandbox.logs[key] is log and not log.closed
            assert module.signal.SIGKILL not in signals
        finally:
            log.close()
    asyncio.run(scenario())


def test_reaped_child_requires_absent_group_before_cleanup_success(monkeypatch):
    async def scenario():
        async def wait():
            return 0
        calls = []
        def absent(pid, action):
            calls.append((pid, action))
            assert action == 0
            raise ProcessLookupError()
        def forbidden_owner():
            raise AssertionError('already reaped leader has no inspectable command')
        monkeypatch.setattr(module.os, 'killpg', absent)
        await module.terminate_process_group(123456, wait, forbidden_owner)
        assert calls == [(123456, 0)]
    asyncio.run(scenario())


def test_kill_uncertainty_retains_native_maps_and_marker(tmp_path, monkeypatch):
    async def scenario():
        _, sandbox, run = owned(tmp_path)
        key, pid = (run['id'], 'shift'), 123456
        async def wait():
            return 0
        sandbox.children[key] = SimpleNamespace(pid=pid, wait=wait)
        log = open(sandbox.path(run['id']) / 'shift.log', 'ab')
        sandbox.logs[key] = log
        marker = sandbox.path(run['id']) / 'shift.process.json'
        marker.write_text(json.dumps({'pid': pid, 'created': 12}))
        signals = []
        monkeypatch.setattr(module.os, 'killpg', lambda ident, action: signals.append(action))
        monkeypatch.setattr(module, 'require_process_group_owner', lambda *args: True)
        loop, real_sleep = asyncio.get_running_loop(), asyncio.sleep
        ticks = [loop.time()]
        async def advance(delay):
            ticks[0] += 9
            await real_sleep(0)
        monkeypatch.setattr(loop, 'time', lambda: ticks[0])
        monkeypatch.setattr(module.asyncio, 'sleep', advance)
        try:
            with pytest.raises(Conflict, match='終了を確認'):
                await sandbox.stop(run['id'])
            assert module.signal.SIGTERM in signals and module.signal.SIGKILL in signals
            assert key in sandbox.children and marker.exists()
            assert sandbox.logs[key] is log and not log.closed
        finally:
            log.close()
    asyncio.run(scenario())
