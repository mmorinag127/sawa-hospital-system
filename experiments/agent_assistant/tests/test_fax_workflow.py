"""Synthetic task orchestration. No real PDF binding, OCR or provider calls."""
import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from experiments.agent_assistant.store import Store, Conflict
from experiments.agent_assistant.tools import Tools, Arguments


class Native:
    def __init__(self, root):
        self.root = root
        self.pdf = b'synthetic document bytes; never sent to OCR'
        self.sources = {}
        self.wfs = {}
        self.sheets = {}
        self.saved = set()
        self.writes = []
        for fax_id in ('A', 'B'):
            order_id = 'O'+fax_id
            self.sources[fax_id] = dict(id=fax_id, content_sha256=hashlib.sha256(self.pdf).hexdigest(), facility_hint='F'+fax_id,
                week_hint='2026-10-19', status='processing', current_order_id=order_id, current_document_id='D'+fax_id, skip_ocr=False)
            self.wfs[order_id] = dict(order_id=order_id, revision=1, state='sheet_review', selected_ocr_result_id='E'+fax_id,
                saved_sheet_id=None, confirmed_snapshot_id='synthetic-snapshot-'+order_id, facility_id='F'+fax_id, week_start='2026-10-19', template_version_id='T1')
            self.sheets[order_id] = dict(header=['品名', '数量'], fields=['menu', 'qty.regular_x'], rows=[['牛乳', 0]])

    def path(self, run_id):
        self.root.mkdir(exist_ok=True)
        return self.root

    def local_jobs(self, run):
        return []

    async def request(self, run, domain, method, path, **kwargs):
        if path == '/outputs/validate-received':
            body=kwargs['body'];wf=self.wfs[body['order_id']]
            return httpx.Response(200,json={'valid':True,**body,'sha256':hashlib.sha256(kwargs['files']['artifact_file'][1]).hexdigest(),'revision':wf['revision'],'confirmed_snapshot_id':wf['confirmed_snapshot_id']})
        if path == '/ingest/pending-faxes':
            items = [dict(id=s['id'], name=s['id']+'.pdf', facility_id=s['facility_hint'], facility=s['facility_hint'], week=s['week_hint'],
                status=s['status'], sha256=s['content_sha256'], order_id=s['current_order_id'], document_id=s['current_document_id'], skip_ocr=False) for s in self.sources.values()]
            return httpx.Response(200, json={'items': items, 'total': len(items)})
        if path.startswith('/ingest/uploads/'):
            return httpx.Response(200, json=self.sources[path.rsplit('/', 1)[-1]])
        order_id = path.split('/')[2]
        wf = self.wfs[order_id]
        if method != 'GET':
            self.writes.append(path)
            if kwargs.get('revision') != wf['revision']:
                return httpx.Response(409, json={'error': 'revision'})
            if path.endswith('/sheet'):
                self.sheets[order_id] = deepcopy(kwargs['body']['sheet'])
                self.saved.add(order_id)
                wf['saved_sheet_id'] = 'S'+order_id
            if path.endswith('/confirm'):
                wf['state'] = 'confirmed'
            wf['revision'] += 1
            return httpx.Response(200, json={'workflow': wf})
        if path.endswith('/document'):
            return httpx.Response(200, content=self.pdf, headers={'content-type': 'application/pdf'})
        if path.endswith('/sheet-source'):
            return httpx.Response(200, json={'selected_ocr_result_id': wf['selected_ocr_result_id'], 'sheet': self.sheets[order_id]})
        if path.endswith('/sheet'):
            assert order_id in self.saved
            return httpx.Response(200, json={'saved_sheet_id': wf['saved_sheet_id'], 'source_ocr_result_id': wf['selected_ocr_result_id'], 'sheet': self.sheets[order_id]})
        return httpx.Response(200, json=wf)


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path/'ledger')
    store.set_budget(100)
    run, _ = store.create('user', 'fax-batch-key', '未処理FAXを処理する', ['hospital'], 100, task_id='hospital_pending_faxes')
    store.update(run['id'], status='running')
    native = Native(tmp_path/'artifacts-root')
    tools = Tools(store, native)
    return store, native, tools, store.get(run['id'])


def test_ready_fax_and_approval_do_not_block_other_ocr(setup):
    async def scenario():
        store, native, tools, run = setup
        native.sources['B']['status'] = 'completed'
        queue = await tools.faxes.refresh(run)
        assert [x['state'] for x in queue['items']] == ['processing', 'ready']
        review = (await tools.faxes.submit(run, 'B', '合成試験：原本とシートの照合結果'))['review']
        assert store.get(run['id'])['status'] == 'running'
        waiter = asyncio.create_task(tools.execute(run['id'], 0, 'wait_for_fax_work', {}, 'wait-1'))
        await asyncio.sleep(.05)
        assert not waiter.done()
        async with tools.lock(run['id']):
            await tools.faxes.decide(run, 'B', review['id'], True, '')
        result = await asyncio.wait_for(waiter, 2)
        assert result['items'][0]['state'] == 'processing'
        assert result['items'][1]['state'] == 'approved'
        native.sources['A']['status'] = 'completed'
        assert (await tools.faxes.refresh(run))['items'][0]['state'] == 'ready'
    asyncio.run(scenario())


@pytest.mark.parametrize('approve',[True,False])
def test_refresh_cannot_overwrite_a_concurrent_human_decision(setup,approve):
    async def scenario():
        store,native,tools,run=setup
        native.sources['A']['status']='completed'
        await tools.faxes.refresh(run)
        review=(await tools.faxes.submit(run,'A','合成原本照合'))['review']
        entered=asyncio.Event();release=asyncio.Event();request=native.request;held=False
        async def delayed(*args,**kwargs):
            nonlocal held
            if args[3]=='/orders/OA/workflow-v2' and not held:
                held=True;entered.set();await release.wait()
            return await request(*args,**kwargs)
        native.request=delayed
        refresh=asyncio.create_task(tools.faxes.refresh(run))
        await asyncio.wait_for(entered.wait(),1)
        await tools.faxes.decide(run,'A',review['id'],approve,'承認' if approve else '数量を再確認してください')
        before=deepcopy(tools.faxes.items(run['id'])[0]);release.set()
        await refresh
        after=tools.faxes.items(run['id'])[0]
        assert after['state']==('approved' if approve else 'needs_correction')
        assert after['decision']==before['decision'] and after['review']==before['review']
    asyncio.run(scenario())


def test_no_self_approval_outside_scope_or_changed_quantity(setup):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        await tools.faxes.refresh(run)
        args = Arguments(ids={'order_id': 'OA'}, body={}, expected_revision=1, reason='合成試験')
        with pytest.raises(Conflict, match='利用者承認'):
            await tools.native(run, 'hospital_confirm_order', args)
        with pytest.raises(Conflict, match='対象外'):
            await tools.native(run, 'hospital_confirm_order', args.model_copy(update={'ids': {'order_id': 'foreign'}}))
        review = (await tools.faxes.submit(run, 'A', '確認結果'))['review']
        await tools.faxes.decide(run, 'A', review['id'], True, '')
        wrong_sheet = deepcopy(review['sheet']); wrong_sheet['rows'][0][1] = 99
        with pytest.raises(Conflict, match='変更内容'):
            await tools.native(run, 'hospital_review_sheet', args.model_copy(update={'body': {'sheet': wrong_sheet}}))
        native.wfs['OA']['revision'] += 1
        with pytest.raises(Conflict, match='版が変わり'):
            await tools.native(run, 'hospital_confirm_order', args)
        assert not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize("saved", [False, True])
def test_human_approves_proposal_then_exact_save_and_confirm(setup, saved):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        await tools.faxes.refresh(run)
        if saved:
            native.saved.add('OA'); native.wfs['OA']['saved_sheet_id'] = 'existing-draft'
        proposal = deepcopy(native.sheets['OA']); proposal['rows'][0][1] = 4
        review = (await tools.faxes.submit(run, 'A', '数量の訂正案', proposal))['review']
        assert review['sheet']['rows'][0][1] == 4
        await tools.faxes.decide(run, 'A', review['id'], True, '')
        # Exact same human request is safe to replay, even as native revisions advance.
        assert (await tools.faxes.decide(run, 'A', review['id'], True, ''))['replayed']
        for name, revision, body in [('hospital_review_sheet', 1, {'sheet': proposal}), ('hospital_save_sheet', 2, {'sheet': proposal}), ('hospital_bag_order', 3, {}), ('hospital_confirm_order', 4, {})]:
            result = await tools.native(run, name, Arguments(ids={'order_id': 'OA'}, expected_revision=revision, body=body, reason='承認された合成データ'))
            assert result['ok']
        assert native.sheets['OA']['rows'][0][1] == 4
        assert (await tools.faxes.refresh(run))['items'][0]['state'] == 'completed'
        with pytest.raises(Conflict, match='確定実行|未確定|帳票'):
            tools.faxes.finish_gate(run['id'], [])
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['revision', 'source', 'ocr', 'quantity_without_revision'])
def test_stale_review_cannot_be_approved(setup, change):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        await tools.faxes.refresh(run)
        review = (await tools.faxes.submit(run, 'A', '確認結果'))['review']
        if change == 'revision': native.wfs['OA']['revision'] += 1
        if change == 'source': native.sources['A']['content_sha256'] = 'changed'
        if change == 'ocr': native.wfs['OA']['selected_ocr_result_id'] = 'other'
        if change == 'quantity_without_revision': native.sheets['OA']['rows'][0][1] = 77
        with pytest.raises(Conflict):
            await tools.faxes.decide(run, 'A', review['id'], True, '')
    asyncio.run(scenario())


def test_queue_is_captured_once_and_stop_wait_does_not_lock(setup):
    async def scenario():
        store, native, tools, run = setup
        await tools.faxes.refresh(run)
        native.sources['C'] = {**native.sources['A'], 'id': 'C'}
        assert len((await tools.faxes.refresh(run))['items']) == 2
        waiter = asyncio.create_task(tools.execute(run['id'], 0, 'wait_for_fax_work', {}, 'wait-stop'))
        await asyncio.sleep(.05)
        store.fence(run['id'])
        async with tools.lock(run['id']):
            pass
        with pytest.raises(Conflict, match='停止'):
            await asyncio.wait_for(waiter, 2)
    asyncio.run(scenario())


def test_task_api_owns_instruction_and_rejects_prompt_override(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from experiments.agent_assistant.app import create_app
    class Client:
        async def close(self): pass
    app = create_app({}, tmp_path/'web', managed_client=Client())
    app.state.store.set_budget(100)
    monkeypatch.setattr(app.state.sandbox, 'prepare', lambda run: None)
    started = []
    async def start(identities, authorization=None):
        started.extend(app.state.store.get(ident) for ident in identities)
    monkeypatch.setattr(app.state.business, 'kick_many', start)
    with TestClient(app) as web:
        web.get('/')
        tasks = web.get('/tasks').json()
        assert tasks[0]['id'] == 'hospital_pending_faxes' and 'instruction' not in tasks[0]
        response = web.post('/runs', json={'request_key': 'selected-task-key', 'task_id': tasks[0]['id'], 'cap_cents': 50})
        assert response.status_code == 201
        assert started[0]['instruction'] == '未処理FAXを処理する'
        assert started[0]['domains'] == ['hospital'] and started[0]['task_id'] == tasks[0]['id']
        rejected = web.post('/runs', json={'request_key': 'override-task-key', 'task_id': tasks[0]['id'], 'instruction': 'overwrite', 'cap_cents': 50})
        assert rejected.status_code == 422
        unknown = web.post('/runs', json={'request_key': 'unknown-task-key', 'task_id': 'unknown', 'cap_cents': 50})
        assert unknown.status_code == 422


def test_rejected_fax_returns_for_correction_and_requires_new_approval(setup):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        await tools.faxes.refresh(run)
        first = (await tools.faxes.submit(run, 'A', '確認結果'))['review']
        await tools.faxes.decide(run, 'A', first['id'], False, '数量を原本と再照合してください')
        queue = await tools.faxes.refresh(run)
        assert queue['items'][0]['state'] == 'needs_correction'
        assert queue['items'][0]['decision']['message'] == '数量を原本と再照合してください'
        second = (await tools.faxes.submit(run, 'A', '再照合した結果'))['review']
        assert second['id'] != first['id']
        with pytest.raises(Conflict, match='古く'):
            await tools.faxes.decide(run, 'A', first['id'], True, '')
        assert (await tools.faxes.refresh(run))['items'][0]['state'] == 'awaiting_approval'
    asyncio.run(scenario())


@pytest.mark.parametrize('value', ['18', '', '要確認', '△'])
def test_sparse_correction_preserves_other_cells_and_native_sheet(value):
    from experiments.agent_assistant.fax_workflow import apply_cell_edits
    sheet = {'fields': ['date_mmdd', 'daypart', 'menu', 'qty.regular_x', 'remarks'],
             'rows': [['01/08', '昼', '白菜', '20', ''], ['01/09', '昼', '白菜', '', '保持']],
             'target_cell_map': {'D1': 'quantity'}}
    original = deepcopy(sheet)
    proposed = apply_cell_edits(sheet, [{'date_mmdd': '01/08', 'daypart': '昼', 'menu': '白菜', 'field': 'qty.regular_x', 'value': value}])
    assert sheet == original
    expected = deepcopy(original); expected['rows'][0][3] = value
    assert proposed == expected


def test_sparse_correction_is_grounded_preserved_and_requires_new_approval(setup):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        native.sheets['OA'] = {'fields': ['date_mmdd', 'daypart', 'menu', 'qty.regular_x', 'qty.unknown_x'],
                               'rows': [['01/08', '昼', '白菜', '20', ''], ['01/09', '昼', '白菜', '', '']],
                               'source_metadata': {'keep': True}}
        original = deepcopy(native.sheets['OA'])
        await tools.faxes.refresh(run)
        first = (await tools.faxes.submit(run, 'A', '原本確認'))['review']
        await tools.faxes.decide(run, 'A', first['id'], False, '白菜18食')
        edits = [{'date_mmdd': '01/08', 'daypart': '昼', 'menu': '白菜', 'field': 'qty.regular_x', 'value': '18'}]
        result = await tools.execute(run['id'], run['generation'], 'request_fax_approval', {'fax_id': 'A', 'summary': '説明だけでは信用しない', 'cell_edits': edits}, 'correct-cell')
        assert result['ok'], result
        review = tools.faxes.item(run['id'], 'A')['review']
        assert review['sheet']['rows'][0][3] == '18'
        assert "'20' → '18'" in review['summary']
        assert '説明だけでは信用しない' not in review['summary']
        assert native.sheets['OA'] == original and not native.writes
        assert (await tools.faxes.submit(run, 'A', '再提出'))['review']['id'] == review['id']
        with pytest.raises(Conflict, match='古く'):
            await tools.faxes.decide(run, 'A', first['id'], True, '')
        await tools.faxes.decide(run, 'A', review['id'], True, '')
        with pytest.raises(Conflict, match='回答済み'):
            await tools.faxes.submit(run, 'A', '変更', cell_edits=[{**edits[0], 'value': '19'}])
        assert native.sheets['OA'] == original
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['flat', 'missing', 'identity', 'duplicate'])
def test_correction_rejects_ambiguous_or_structurally_changed_rows(change):
    from experiments.agent_assistant.fax_workflow import apply_cell_edits, validate_review_sheet
    sheet = {'fields': ['date_mmdd', 'daypart', 'menu', 'qty.regular_x'], 'rows': [['01/08', '昼', '白菜', '20']]}
    proposed = deepcopy(sheet)
    if change == 'duplicate':
        sheet['rows'] *= 2
        with pytest.raises(Conflict, match='一意'):
            apply_cell_edits(sheet, [{'date_mmdd': '01/08', 'daypart': '昼', 'menu': '白菜', 'field': 'qty.regular_x', 'value': '18'}])
        return
    if change == 'flat': proposed['rows'] = proposed['rows'][0]
    if change == 'missing': proposed['rows'] = []
    if change == 'identity': proposed['rows'][0][2] = '別献立'
    with pytest.raises(Conflict):
        validate_review_sheet(sheet, proposed)


def test_proposal_cannot_change_hidden_mapping_or_columns(setup):
    async def scenario():
        _, native, tools, run = setup
        native.sources['A']['status'] = 'completed'
        await tools.faxes.refresh(run)
        sheet = deepcopy(native.sheets['OA'])
        sheet['target_cell_map'] = {'A1': 'other-facility'}
        with pytest.raises(Conflict, match='正本情報'):
            await tools.faxes.submit(run, 'A', '数量照合', sheet)
    asyncio.run(scenario())


def test_wait_authorization_expiry_pauses_for_login_without_resuming_model(setup):
    async def scenario():
        store, _, tools, run = setup
        async def expired(run):
            raise Conflict('再ログインしてください')
        tools.authorization_validator = expired
        result = await tools.execute(run['id'], 0, 'wait_for_fax_work', {}, 'expired-wait')
        assert result['needs_operator_confirmation']
        assert store.get(run['id'])['status'] == 'waiting_input'
    asyncio.run(scenario())


def test_every_fax_completes_with_three_outputs_after_human_answers(setup):
    async def scenario():
        store,native,tools,run=setup
        original=native.request
        async def request(run,domain,method,path,**kwargs):
            if method=='GET' and path.startswith('/outputs/'):
                return httpx.Response(200,content=b'synthetic output, not a real workbook',headers={'content-type':'application/octet-stream'})
            return await original(run,domain,method,path,**kwargs)
        native.request=request
        for source in native.sources.values():source['status']='completed'
        await tools.faxes.refresh(run)
        outputs=[];evidence=[]
        for fax_id in ('A','B'):
            order_id='O'+fax_id
            review=(await tools.faxes.submit(run,fax_id,'合成数量を確認してください'))['review']
            await tools.faxes.decide(run,fax_id,review['id'],True,'')
            for name,body in [('hospital_review_sheet',{'sheet':review['sheet']}),('hospital_save_sheet',{'sheet':review['sheet']}),('hospital_bag_order',{}),('hospital_confirm_order',{})]:
                result=await tools.execute(run['id'],0,name,{'parameters':{'ids':{'order_id':order_id},'expected_revision':native.wfs[order_id]['revision'],'body':body,'reason':'人が承認した合成数量'}},name+fax_id)
                assert result.get('ok'),result
            for name in ('hospital_export_labels','hospital_export_delivery_notes','hospital_export_aggregate'):
                result=await tools.execute(run['id'],0,name,{'parameters':{'query':{'order_id':order_id}}},name+fax_id)
                assert result.get('ok'),result
                outputs.append(result['artifact']['id'])
            key='read-'+fax_id
            result=await tools.execute(run['id'],0,'hospital_get_workflow',{'parameters':{'ids':{'order_id':order_id}}},key)
            assert result.get('ok'),result
            evidence.append(key)
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成FAX二件の確定と全帳票を確認','evidence_commands':evidence,'artifact_ids':outputs},'finish-all-faxes')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed'
        assert all(x['rows'][0][1]==0 for x in native.sheets.values())
    asyncio.run(scenario())
