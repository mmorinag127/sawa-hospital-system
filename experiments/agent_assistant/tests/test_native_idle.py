"""Synthetic native-writer scheduling; no OCR or live provider calls."""
import asyncio

import httpx
import pytest

from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_trial import prepared
from experiments.agent_assistant.tools import Arguments


@pytest.mark.parametrize('state', ['accepted', 'unknown'])
def test_native_writer_defers_mutations_without_sending_http(tmp_path, monkeypatch, state):
    async def scenario():
        _, sandbox, tools, run = prepared(tmp_path)
        jobs = [{'domain':'hospital','state':state}]
        monkeypatch.setattr(sandbox, 'local_jobs', lambda current: jobs)
        requests = []
        async def request(*args, **kwargs):
            requests.append((args[2], args[3], kwargs.get('revision')))
            return httpx.Response(200, json={'revision':2})
        monkeypatch.setattr(sandbox, 'request', request)
        args = Arguments(ids={'order_id':'O','ocr_result_id':'E'}, expected_revision=1, reason='Synthetic source selection')
        deferred = await tools.native(run, 'hospital_select_ocr', args)
        assert deferred['native_busy'] and not deferred['ok']
        assert requests == []
        assert (await tools.native(run, 'hospital_get_workflow', Arguments(ids={'order_id':'O'})))['ok']
        assert requests == [('GET','/orders/O/workflow-v2',None)]
        jobs[0]['state'] = 'returned'
        assert (await tools.native(run, 'hospital_select_ocr', args))['ok']
        assert requests[-1] == ('POST','/orders/O/workflow-v2/ocr-results/E/select',1)
    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['returned', 'failed'])
def test_native_idle_wait_releases_tool_lock_and_finishes_only_after_writer(tmp_path, monkeypatch, terminal):
    async def scenario():
        store, sandbox, tools, run = prepared(tmp_path)
        jobs = [{'domain':'hospital','state':'accepted'}]
        started = asyncio.Event()
        async def start(current):
            assert current['id'] == run['id']
            started.set()
        monkeypatch.setattr(sandbox,'start',start)
        monkeypatch.setattr(sandbox,'local_jobs',lambda current: jobs)
        task = asyncio.create_task(tools.execute(run['id'],run['generation'],
            'wait_for_native_idle',{'domain':'hospital'},'synthetic-wait'))
        await asyncio.wait_for(started.wait(),1)
        async with tools.lock(run['id']):
            assert not task.done()
            assert store.get(run['id'])['status'] == 'running'
            jobs[0]['state'] = terminal
        result = await asyncio.wait_for(task,2)
        assert result['native_idle'] and result['ok']
        assert await tools.execute(run['id'],run['generation'],
            'wait_for_native_idle',{'domain':'hospital'},'synthetic-wait') == result
    asyncio.run(scenario())


def test_native_idle_wait_obeys_stop_and_does_not_wait_for_other_domain(tmp_path, monkeypatch):
    async def scenario():
        store, sandbox, tools, run = prepared(tmp_path,domains=('hospital','shift'))
        jobs = [{'domain':'shift','state':'accepted'}]
        async def start(current):
            pass
        monkeypatch.setattr(sandbox,'start',start)
        monkeypatch.setattr(sandbox,'local_jobs',lambda current:jobs)
        assert (await tools.execute(run['id'],run['generation'],
            'wait_for_native_idle',{'domain':'hospital'},'other-domain'))['native_idle']
        task = asyncio.create_task(tools.execute(run['id'],run['generation'],
            'wait_for_native_idle',{'domain':'shift'},'stop-wait'))
        await asyncio.sleep(.02)
        store.fence(run['id'],expected_generation=run['generation'])
        with pytest.raises(Conflict,match='停止'):
            await asyncio.wait_for(task,2)
        with pytest.raises(ValueError):
            await tools.wait_native_idle(run['id'],run['generation'],{'domain':'unknown'},'wrong-domain')
    asyncio.run(scenario())
