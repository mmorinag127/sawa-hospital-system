"""Actual controller/Tools against synthetic native transport; no paid model/OCR proof."""
import asyncio
from copy import deepcopy

import httpx
import pytest

from experiments.agent_assistant.business_contracts import CONTRACTS, model_definitions, server_tools
from experiments.agent_assistant.business_workflow import BusinessWorkflow
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tasks import public_tasks
from experiments.agent_assistant.tests.test_selected_tasks import setup, shift, lunch, get, QUERY


class NoModel:
    def __init__(self):
        self.tasks = {}
        self.started = []
        self.followed = []

    def for_run(self, ident):
        return None

    async def start(self, run):
        self.started.append(run['id'])
        raise AssertionError('Native completion must not ask a model what to do next')

    async def refresh_usage(self, *args, **kwargs):
        raise AssertionError('No model request was sent')


def attach(store, native, tools, run):
    engine = BusinessWorkflow(store, tools, NoModel())
    directory = native.path(run['id']) / 'data' / run['domains'][0]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'database.sqlite3').write_bytes(b'synthetic working-copy marker')
    if not hasattr(native, 'start'):
        async def start(run):
            pass
        native.start = start
    engine.initialize(store.get(run['id']))
    return engine, store.get(run['id'])


def test_every_visible_task_has_a_server_contract_and_models_cannot_finalize(tmp_path):
    assert set(CONTRACTS) == {task['id'] for task in public_tasks()}
    assert len(CONTRACTS) == 12
    async def scenario():
        for task in CONTRACTS:
            store, native, tools, run = setup(tmp_path / task, task, [])
            engine, run = attach(store, native, tools, run)
            names = {definition['name'] for definition in model_definitions(tools, run)}
            assert not names.intersection(server_tools(task))
            for operation in server_tools(task):
                with pytest.raises(Conflict, match='業務進行サーバー'):
                    await tools.execute(run['id'], 0, operation, {}, 'forged-' + operation)
            assert not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize('task', ['hospital_outputs', 'shift_check', 'shift_export', 'lunch_month_exports'])
def test_selected_native_task_reaches_review_and_finishes_after_approval_without_model(tmp_path, task):
    async def scenario():
        if task.startswith('shift_'):
            store, native, tools, run = shift(tmp_path, task)
        elif task == 'lunch_month_exports':
            store, native, tools, run = lunch(tmp_path, task)
        else:
            store, native, tools, run = setup(tmp_path, task, [{'key': 'O1', 'label': '合成確定注文', 'data': {'id': 'O1'}}])
            native.data['/orders/O1/workflow-v2'] = {'order_id': 'O1', 'state': 'confirmed', 'revision': 1, 'facility_id': 'F1', 'week_start': '2026-11-02', 'confirmed_snapshot_id': 'synthetic-snapshot', 'blockers': []}
        engine, run = attach(store, native, tools, run)
        assert await engine.advance(run['id']) is False
        review = tools.flow.review(run['id'])
        assert review['state'] == 'awaiting_approval'
        assert store.get(run['id'])['status'] != 'completed'
        before = len(native.writes)
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status'] == 'completed'
        assert len(native.writes) == before
        with store.read() as db:
            count = db.execute('SELECT COUNT(*) FROM artifacts WHERE run_id=?', (run['id'],)).fetchone()[0]
        assert count == len(CONTRACTS[task].deliverables) if task != 'shift_check' else count == 0
        assert not engine.providers.started
        assert store.get(run['id'])['reserved'] == 0
        # Re-entering the same completed business never repeats writes/outputs.
        assert await engine.advance(run['id']) is False
        with store.read() as db:
            assert db.execute('SELECT COUNT(*) FROM artifacts WHERE run_id=?', (run['id'],)).fetchone()[0] == count
    asyncio.run(scenario())


@pytest.mark.parametrize('task', ['shift_next_month', 'shift_wishes', 'shift_absence'])
def test_shift_generation_approval_and_export_are_server_owned(tmp_path, task):
    async def scenario():
        store, native, tools, run = shift(tmp_path, task)
        native.data['/solve-runs'] = []
        native.data['/solve-runs/501']['command_precondition'] = {'candidate_id': 77, 'candidate_revision': 'a' * 64, 'input_revision': 'b' * 64}
        native.data['/constraints']['collection_revision'] = 1
        if task == 'shift_absence':
            context = run['task_inputs']
            context['inputs']['absence_date'] = '2026-11-02'
            context['targets'] = [{'key': '1', 'label': '合成人員', 'data': {'id': 1, 'name': '合成人員'}}]
            store.update(run['id'], task_inputs=context)
        transport = native.request
        async def request(run, domain, method, path, **kwargs):
            if path == '/solve' and method == 'POST':
                native.writes.append((path, deepcopy(kwargs)))
                return httpx.Response(200, json={'solve_run_id': 501})
            if path == '/constraints' and method == 'POST':
                native.data[path]['items'].append({'id': 10, **kwargs['body']})
            return await transport(run, domain, method, path, **kwargs)
        native.request = request
        engine, run = attach(store, native, tools, store.get(run['id']))
        assert await engine.advance(run['id']) is False
        review = tools.flow.review(run['id'])
        assert review['state'] == 'awaiting_approval'
        assert native.data['/solve-runs/501']['confirmed'] is False
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status'] == 'completed'
        assert native.data['/solve-runs/501']['confirmed'] is True
        assert sum(path.endswith('/confirm') for path, _ in native.writes) == 1
        assert not engine.providers.started
    asyncio.run(scenario())


def test_daily_three_outputs_finish_after_approval_even_without_a_model_connection(tmp_path):
    from experiments.agent_assistant.tests.test_daily_completion import setup as daily
    from experiments.agent_assistant.tests.daily_question_fixture import complete
    async def scenario():
        store, native, tools, run = daily(tmp_path)
        await complete(tools, run)
        engine, run = attach(store, native, tools, store.get(run['id']))
        result = await engine.call(run, 'prepare_lunch_daily_review', {}, 'test-preparation')
        assert result['queued_for_human']
        review = tools.flow.review(run['id'])
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status'] == 'completed'
        assert not engine.providers.started
        with store.read() as db:
            assert db.execute('SELECT COUNT(*) FROM artifacts WHERE run_id=?', (run['id'],)).fetchone()[0] == 3
    asyncio.run(scenario())


@pytest.mark.parametrize('after_projection', [False, True])
def test_confirmed_native_response_survives_projection_loss_and_controller_restart(tmp_path, after_projection):
    async def scenario():
        store, native, tools, run = shift(tmp_path, 'shift_wishes')
        native.data['/solve-runs'] = []
        native.data['/solve-runs/501']['command_precondition'] = {'candidate_id': 77, 'candidate_revision': 'a' * 64, 'input_revision': 'b' * 64}
        native.data['/constraints']['collection_revision'] = 1
        transport = native.request
        async def request(run, domain, method, path, **kwargs):
            if path == '/solve' and method == 'POST':
                native.writes.append((path, deepcopy(kwargs)))
                return httpx.Response(200, json={'solve_run_id': 501})
            return await transport(run, domain, method, path, **kwargs)
        native.request = request
        engine, run = attach(store, native, tools, run)
        await engine.advance(run['id'])
        review = tools.flow.review(run['id'])
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        original = tools.native_result
        async def interrupted(run, name, args, response):
            if name == 'shift_confirm':
                if after_projection:
                    await original(run, name, args, response)
                raise ConnectionError('synthetic gateway loss after native commit')
            return await original(run, name, args, response)
        tools.native_result = interrupted
        with pytest.raises(ConnectionError):
            await engine.advance(run['id'])
        assert native.data['/solve-runs/501']['confirmed']
        assert store.get(run['id'])['status'] != 'completed'
        tools.native_result = original
        from experiments.agent_assistant.durable import PostgresStore
        # Exercise production fencing SQL on this isolated ledger. Actual
        # PostgreSQL snapshot/ownership checks remain in test_durable_postgres.
        PostgresStore.recover(store)
        restarted = BusinessWorkflow(store, tools, NoModel())
        await restarted.advance(run['id'])
        assert store.get(run['id'])['status'] == 'completed'
        assert sum(path.endswith('/confirm') for path, _ in native.writes) == 1
        assert any(event['kind'] == 'business_write_recovery' for event in store.events(run['id']))
        assert tools.flow.review(run['id'])['decision']['approve']
        assert not restarted.providers.started
    asyncio.run(scenario())


def test_http_approval_response_loss_and_exact_retry_do_not_repeat_exports(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from experiments.agent_assistant.app import create_app
    monkeypatch.setenv('SAWA_TRIAL_OPERATOR_ACCOUNT', 'operator')
    store, native, tools, run = shift(tmp_path, 'shift_export')
    engine, run = attach(store, native, tools, run)
    asyncio.run(engine.advance(run['id']))
    review = tools.flow.review(run['id'])
    app = create_app({}, store.root, managed_client=SimpleNamespace(close=AsyncMock()))
    actual = app.state.business
    app.state.tools.sandbox = native
    app.state.tools.authorizations[run['id']] = 'Bearer synthetic'
    wakeups = []
    actual.kick = lambda ident, authorization=None: wakeups.append(ident)
    lost = False
    @app.middleware('http')
    async def disconnect_after_acceptance(request, call_next):
        nonlocal lost
        response = await call_next(request)
        if request.url.path.endswith('/decision') and not lost:
            lost = True
            return JSONResponse({'detail': 'synthetic response delivery loss'}, status_code=503)
        return response
    with TestClient(app) as web:
        web.headers['X-Trial-Token'] = app.state.token
        body = {'review_id': review['id'], 'approve': True, 'message': ''}
        path = '/runs/' + run['id'] + '/decision'
        # A gateway restart retains the review; restore its actionable native phase.
        app.state.store.update(run['id'], status='running')
        assert web.post(path, json=body).status_code == 503
        assert app.state.tools.flow.review(run['id'])['state'] == 'approved'
        asyncio.run(actual.advance(run['id']))
        assert app.state.store.get(run['id'])['status'] == 'completed'
        before = native.reads.count('/solve-runs/501/export')
        response = web.post(path, json=body)
        assert response.status_code == 200 and response.json()['replayed']
        asyncio.run(actual.advance(run['id']))
        assert native.reads.count('/solve-runs/501/export') == before == 1
        assert not native.writes
        detail = web.get('/runs/' + run['id']).json()
        assert detail['business']['state'] == 'completed'
        assert len(detail['artifacts']) == 1
        assert detail['task_review']['decision'] == {'approve': True, 'message': ''}


@pytest.mark.parametrize('task', ['lunch_import', 'lunch_procurement'])
def test_import_and_procurement_use_the_saved_approved_source_for_native_completion(tmp_path, task):
    from experiments.agent_assistant.tests.test_selected_tasks import record
    async def scenario():
        store, native, tools, run = lunch(tmp_path, task)
        engine, run = attach(store, native, tools, run)
        actions = []
        if task == 'lunch_import':
            record(tools, run, 'lunch_import_source', {'source_document_id': 'doc-synthetic'}, {'query': QUERY})
            evidence = [await get(tools, run, 'lunch_get_source_document', {'document_id': 'doc-synthetic'}, QUERY)]
            menus = [{'facility_name': '合成試験校', 'service_date': '2026-11-02', 'name': '合成献立', 'servings': 10, 'version': 1, 'status': 'draft', 'assignees': [], 'ingredients': []}]
            actions = [{'operation': 'lunch_confirm_import', 'parameters': {'query': QUERY, 'body': {'source_document_id': 'doc-synthetic', 'menus': menus}, 'reason': '合成原本の人による承認'}}]
        else:
            native.data['/api/school-lunch/monthly-procurement']['fingerprint'] = 'f' * 64
            evidence = [await get(tools, run, 'lunch_get_procurement', query=QUERY)]
        await tools.flow.submit(run, '原本・登録済み内容を確認してください', evidence, actions)
        review = tools.flow.review(run['id'])
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        engine.checkpoint(run['id'], phase='approval')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status'] == 'completed'
        if task == 'lunch_import':
            assert sum(path.endswith('/confirm') for path, _ in native.writes) == 1
            assert native.data['/api/school-lunch/menu-executions'][0]['servings'] == 10
        assert not engine.providers.started
    asyncio.run(scenario())


@pytest.mark.parametrize('task', ['hospital_pending_faxes', 'hospital_review_faxes'])
def test_fax_quantity_approvals_drive_confirmation_and_three_outputs_on_server(tmp_path, task):
    from experiments.agent_assistant.tests.test_fax_workflow import Native
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.tools import Tools
    async def scenario():
        store = Store(tmp_path / 'ledger')
        store.set_budget(100)
        native = Native(tmp_path / 'native')
        del native.sources['B']
        native.sources['A']['status'] = 'completed'
        items = (await native.request({}, 'hospital', 'GET', '/ingest/pending-faxes')).json()['items']
        targets = [{'key': item['id'], 'label': item['name'], 'data': item} for item in items]
        run, _ = store.create('operator', 'owned-fax-task', '合成FAX処理', ['hospital'], 100, task_id=task, task_inputs={'inputs': {}, 'targets': targets})
        store.update(run['id'], status='running')
        tools = Tools(store, native)
        transport = native.request
        async def request(run, domain, method, path, **kwargs):
            if method == 'GET' and path.startswith('/outputs/'):
                return httpx.Response(200, content=b'synthetic output bytes; not real workbook proof', headers={'content-type': 'application/octet-stream'})
            return await transport(run, domain, method, path, **kwargs)
        native.request = request
        engine, run = attach(store, native, tools, store.get(run['id']))
        await tools.faxes.refresh(run)
        result = await tools.faxes.submit(run, 'A', '合成原本と数量の照合結果')
        await tools.faxes.decide(run, 'A', result['review']['id'], True, '')
        engine.checkpoint(run['id'], phase='approval')
        assert await engine.advance(run['id']) is False
        assert store.get(run['id'])['status'] == 'completed'
        assert sum(path.endswith('/confirm') for path in native.writes) == 1
        with store.read() as db:
            # Original PDF stays in the archive, separately from the three outputs.
            assert db.execute('SELECT COUNT(*) FROM artifacts WHERE run_id=?', (run['id'],)).fetchone()[0] == 4
        completion = [event['payload'] for event in store.events(run['id']) if event['kind'] == 'completion_evidence'][-1]
        assert len(completion['artifact_ids']) == 3
        assert not engine.providers.started
    asyncio.run(scenario())


def test_unknown_native_write_blocks_all_sibling_writes_without_stopping_or_losing_approval(tmp_path):
    async def scenario():
        store, native, tools, run = shift(tmp_path)
        engine, run = attach(store, native, tools, run)
        store.command(run['id'], 'unknown-solve', {'name': 'shift_solve', 'arguments': {'parameters': {'body': {'target_month': '2026-11'}}}})
        with pytest.raises(Conflict, match='未確定'):
            await engine.call(run, 'shift_preflight', {'parameters': {'body': {'target_month': '2026-11'}, 'reason': '合成検証'}}, 'sibling')
        assert not native.writes
        assert store.get(run['id'])['status'] == 'running'
        assert not any(event['kind'] == 'rollback' for event in store.events(run['id']))
    asyncio.run(scenario())


@pytest.mark.parametrize('proof', ['missing', 'wrong_hash', 'cancelled_generation'])
def test_recovery_never_replays_a_write_without_its_matching_response(tmp_path, proof):
    import hashlib
    import json
    from experiments.agent_assistant.native_recovery import reconcile
    async def scenario():
        store, native, tools, run = shift(tmp_path)
        engine, run = attach(store, native, tools, run)
        request = {'name': 'shift_confirm', 'arguments': {'parameters': {'ids': {'run_id': 501}}}}
        store.command(run['id'], 'uncertain-confirm', request)
        if proof != 'missing':
            store.event(run['id'], 'native_response_receipt', {'command_id': 'uncertain-confirm', 'generation': run['generation'],
                'name': 'shift_confirm', 'request_sha256': 'wrong' if proof == 'wrong_hash' else hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'parameters': {'ids': {'run_id': 501}}, 'status': 200, 'data': {'confirmed': True, 'solve_run_id': 501}})
        if proof == 'cancelled_generation':
            store.fence(run['id'])
        with pytest.raises(Conflict, match='保存応答が未確定'):
            await reconcile(engine, store.get(run['id']))
        assert not native.writes
        assert store.get(run['id'])['status'] != 'completed'
    asyncio.run(scenario())


def test_more_than_eight_layouts_remain_selectable_without_internal_ids(tmp_path):
    from experiments.agent_assistant.conversation import pending_question
    async def scenario():
        store, native, tools, run = lunch(tmp_path, 'lunch_daily_bundle')
        context = run['task_inputs']
        context['inputs'].pop('layout_id')
        context['inputs'].pop('layout_version')
        context['inputs']['layout_choices'] = [{'id': i, 'version': 3, 'name': '調理室図面' + str(i)} for i in range(1, 10)]
        store.update(run['id'], task_inputs=context)
        engine, run = attach(store, native, tools, store.get(run['id']))
        engine.kick = lambda *args: None
        assert await engine.choose_layout(run)
        question = pending_question(store.events(run['id']))
        assert len(question['options']) == 8
        engine.answer_layout(store.get(run['id']), 'ほかの図面を見る', question['question_id'], 'ほかの図面を見る')
        engine.activate(store.get(run['id']))
        assert await engine.choose_layout(store.get(run['id']))
        question = pending_question(store.events(run['id']))
        assert '調理室図面9' in question['options']
        engine.answer_layout(store.get(run['id']), '調理室図面9', question['question_id'], '調理室図面9')
        assert store.get(run['id'])['task_inputs']['inputs']['layout_id'] == 9
        assert store.get(run['id'])['task_inputs']['inputs']['layout_version'] == 3
        assert not native.writes
    asyncio.run(scenario())


def test_a_late_controller_cannot_resurrect_an_administrator_cancelled_generation(tmp_path):
    store, native, tools, run = shift(tmp_path)
    engine, run = attach(store, native, tools, run)
    store.fence(run['id'])
    with pytest.raises(Conflict, match='遅延'):
        engine.activate(run)
    assert store.get(run['id'])['status'] == 'stopping'
    projection = engine.projection(store.get(run['id']), {'state': 'awaiting_approval'})
    assert projection['state'] == 'archived' and projection['next_action'] == 'none'


def test_transient_outage_does_not_exhaust_automatic_business_reconciliation(tmp_path, monkeypatch):
    async def scenario():
        store, native, tools, run = shift(tmp_path, 'shift_export')
        engine, run = attach(store, native, tools, run)
        original = native.request
        reads = 0
        async def unavailable(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads <= 5:
                raise httpx.ConnectError('synthetic native API restart')
            return await original(*args, **kwargs)
        native.request = unavailable
        async def elapsed(_):
            engine.checkpoint(run['id'], next_attempt=0)
        monkeypatch.setattr('experiments.agent_assistant.business_workflow.asyncio.sleep', elapsed)
        await engine.drive(run['id'])
        review = tools.flow.review(run['id'])
        assert reads > 5 and review['state'] == 'awaiting_approval'
        assert engine.projection(store.get(run['id']), review)['next_action'] == 'approve'
        assert not native.writes and not engine.providers.started
        await tools.flow.decide(store.get(run['id']), review['id'], True, '')
        await engine.drive(run['id'])
        assert store.get(run['id'])['status'] == 'completed'
    asyncio.run(scenario())


def test_internal_ids_and_recovery_are_never_presented_as_operator_questions(tmp_path):
    async def scenario():
        store, native, tools, run = lunch(tmp_path, 'lunch_daily_bundle')
        engine, run = attach(store, native, tools, run)
        result = await tools.execute(run['id'], 0, 'ask_operator', {'question': 'draft_idを選んで停止を再確認してください', 'options': ['再開']}, 'bad-question')
        assert result.get('error')
        assert store.get(run['id'])['status'] == 'running'
        assert not any(event['kind'] == 'tool_result' and event['payload'].get('result', {}).get('question') for event in store.events(run['id']))
    asyncio.run(scenario())
