"""Synthetic transport checks for bounded native reads; no business DB or model."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.task_flow import TaskFlow


def flow(request):
    return TaskFlow(SimpleNamespace(store=None, sandbox=SimpleNamespace(request=request), authorizations={}))


def test_constraints_use_the_canonical_custom_period_not_calendar_month():
    calls = []

    async def request(run, domain, method, path, **kwargs):
        calls.append((path, kwargs['params']))
        return httpx.Response(200, json={'start_date': '2026-10-20', 'end_date_exclusive': '2026-11-20'}
                              if path == '/planning/period' else {'items': []})

    run = {'id': 'owned', 'task_inputs': {'inputs': {'month': '2026-11'}}}
    assert asyncio.run(flow(request).read(run, 'shift_get_constraints')) == {'items': []}
    assert calls == [('/planning/period', {'month': '2026-11'}),
                     ('/constraints', {'date_from': '2026-10-20', 'date_to': '2026-11-20'})]


@pytest.mark.parametrize('stalled', [False, True])
def test_history_pages_do_not_truncate_old_runs_or_repeat_a_cursor(stalled):
    calls = []

    async def request(run, domain, method, path, **kwargs):
        query = kwargs['params']
        calls.append(dict(query))
        upper = 402 if stalled else query.get('before_id', 402)
        return httpx.Response(200, json=[{'id': ident} for ident in range(upper - 1, max(0, upper - 201), -1)])

    read = flow(request).read({'id': 'owned'}, 'shift_get_runs')
    if stalled:
        with pytest.raises(Conflict, match='ページが進みません'):
            asyncio.run(read)
        assert len(calls) == 2
    else:
        result = asyncio.run(read)
        assert [item['id'] for item in result] == list(range(401, 0, -1))
        assert calls == [{'limit': 200}, {'limit': 200, 'before_id': 202}, {'limit': 200, 'before_id': 2}]
