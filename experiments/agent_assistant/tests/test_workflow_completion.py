import asyncio
from copy import deepcopy
import json
import pytest

from experiments.agent_assistant.mcp_server import build_mcp, call_local
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tool_projection import project, read
from experiments.agent_assistant.tools import Arguments
from experiments.agent_assistant.tests.test_selected_tasks import shift, shift_evidence, record


def test_malformed_mcp_arguments_return_repairable_error_without_execution(tmp_path):
    async def scenario():
        store, native, tools, run = shift(tmp_path)
        result = await call_local(build_mcp(tools), tools, run['id'], 0, 'shift_solve',
            {'parameters': {'target_month': '2026-11', 'body': {'target_month': '2026-11'}}}, 'invalid-wrapper')
        assert result['input_validation_failed'] and result['executed'] is False
        assert not native.writes
        store.fence(run['id'])
        with pytest.raises(Conflict):
            await call_local(build_mcp(tools),tools,run['id'],0,'shift_solve',{'parameters':{'target_month':'2026-11'}},'late-invalid')
    asyncio.run(scenario())


def test_mcp_wire_limit_keeps_full_server_receipt_and_isolates_reads(tmp_path):
    store,native,tools,run=shift(tmp_path)
    data={'rows':[['20',''] for _ in range(56)],'evidence':'x'*100000}
    command=record(tools,run,'hospital_get_sheet_source',data)
    full={'ok':True,'command_id':command,'data':deepcopy(data)}
    result=project(full)
    assert result['result_stored'] and len(json.dumps(result))<48000
    assert full['data']==data
    assert read(tools,run,{'command_id':command,'pointer':'/data/rows','offset':50,'count':10})['data']==data['rows'][50:]
    with pytest.raises(ValueError):read(tools,run,{'command_id':'other-run','pointer':''})


def test_oversized_fax_review_retains_every_target_and_actual_human_decision():
    item={'fax_id':'synthetic-fax','state':'approved','snapshot':{'facility_id':'F1'},'decision':{'approve':True,'review_id':'human-review'},'review':{'sheet':{'metadata':'x'*100000}}}
    original={'ok':True,'command_id':'queue','items':[deepcopy(item)]}
    result=project(original)
    assert result['items'][0]=={key:item[key] for key in ('fax_id','state','snapshot','decision')}
    assert result['result_stored'] and len(json.dumps(result))<48000
    assert original['items'][0]['review']==item['review']


@pytest.mark.parametrize('change',['own_confirmation','unrelated_confirmation','changed_business','removed_row'])
def test_inventory_confirmation_only_rebinds_approved_run(tmp_path,change):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        inventory=[{'id':501,'confirmed':False,'confirmed_at':None,'status':'succeeded'}, {'id':999,'confirmed':False,'confirmed_at':None,'status':'succeeded'}]
        native.data['/solve-runs']=deepcopy(inventory)
        evidence=await shift_evidence(tools,run)
        evidence.append((await tools.execute(run['id'],0,'shift_get_runs',{'parameters':{}},'inventory'))['command_id'])
        params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':{},'reason':'利用者承認'}
        await tools.flow.submit(run,'勤務案を確認してください',evidence,[{'operation':'shift_confirm','parameters':params}])
        review=tools.flow.review(run['id']);await tools.flow.decide(run,review['id'],True,'承認')
        native.data['/solve-runs/501']['confirmed']=True
        native.data['/solve-runs'][0].update(confirmed=True,confirmed_at='now')
        if change=='unrelated_confirmation':native.data['/solve-runs'][1]['confirmed']=True
        if change=='changed_business':native.data['/solve-runs'][0]['status']='failed'
        if change=='removed_row':native.data['/solve-runs'].pop()
        if change=='own_confirmation':
            await tools.flow.after(run,'shift_confirm',Arguments.model_validate(params),{'confirmed':True,'solve_run_id':501})
            assert tools.flow.review(run['id'])['state']=='approved'
        else:
            with pytest.raises(Conflict):await tools.flow.after(run,'shift_confirm',Arguments.model_validate(params),{'confirmed':True,'solve_run_id':501})
    asyncio.run(scenario())


def test_helpers_reject_unapproved_completion_and_late_commands(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        result=await tools.execute(run['id'],0,'complete_shift_work',{},'premature')
        assert '承認' in result['error'] and not native.writes
        store.fence(run['id'])
        with pytest.raises(Conflict):await tools.execute(run['id'],0,'prepare_shift_review',{},'late')
    asyncio.run(scenario())


def test_fax_helper_keeps_canonical_sheet_and_requires_each_human_approval(tmp_path):
    from experiments.agent_assistant.tests.test_fax_workflow import Native
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.tools import Tools
    async def scenario():
        store=Store(tmp_path/'ledger');store.set_budget(100)
        run,_=store.create('user','fax-helper','FAX',['hospital'],100,task_id='hospital_pending_faxes')
        store.update(run['id'],status='running');run=store.get(run['id'])
        native=Native(tmp_path/'files');tools=Tools(store,native)
        native.sources['A']['status']='completed'
        native.sheets['OA']['canonical_metadata']={'never_transcribe':'x'*100000}
        original=deepcopy(native.sheets['OA'])
        result=await tools.execute(run['id'],0,'prepare_fax_review',{'fax_id':'A'},'prepare')
        assert result['ok'] and result['sheet']['rows']==original['rows']
        assert 'canonical_metadata' not in result['sheet']
        assert native.sheets['OA']==original
        assert project({**result,'extra':'x'*100000})['command_id']=='prepare'
        # Replaying a completed prepare reuses its sealed original artifact.
        assert await tools.execute(run['id'],0,'prepare_fax_review',{'fax_id':'A'},'prepare')==result
        denied=await tools.execute(run['id'],0,'complete_fax_work',{},'premature')
        assert '承認' in denied['error'] and not native.writes
        await tools.faxes.submit(run,'A','原本照合済み')
        review=tools.faxes.items(run['id'])[0]['review']
        await tools.faxes.decide(run,'A',review['id'],True,'承認')
        denied=await tools.execute(run['id'],0,'complete_fax_work',{},'partial-approval')
        # All approvals must be checked before any batch mutation.
        assert denied.get('error') and not native.writes
    asyncio.run(scenario())


def test_native_operator_wait_remains_a_question_in_helper(tmp_path):
    import httpx
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        async def rejected(*args,**kwargs):return httpx.Response(409,json={'detail':'operator review required'})
        native.request=rejected
        result=await tools.execute(run['id'],0,'prepare_shift_review',{},'native-conflict')
        assert result['needs_operator_confirmation'] and result['question']
        assert store.get(run['id'])['status']=='waiting_input'
        assert not native.writes
    asyncio.run(scenario())


def test_unknown_native_write_is_never_replayed_by_a_new_helper_call(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        store.command(run['id'],'unknown-write',{'name':'shift_solve','arguments':{'parameters':{'body':{'target_month':'2026-11'}}}})
        result=await tools.execute(run['id'],0,'prepare_shift_review',{},'new-helper')
        assert '結果未確定' in result['error'] and not native.writes
    asyncio.run(scenario())


def test_fax_export_retry_does_not_repeat_successful_native_writes(tmp_path):
    from experiments.agent_assistant.tests.test_fax_workflow import Native
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.tools import Tools
    async def scenario():
        store=Store(tmp_path/'ledger');store.set_budget(100)
        run,_=store.create('user','resume-fax','FAX',['hospital'],100,task_id='hospital_pending_faxes')
        store.update(run['id'],status='running');run=store.get(run['id'])
        native=Native(tmp_path/'files');del native.sources['B']
        native.sources['A']['status']='completed'
        tools=Tools(store,native)
        request=native.request
        async def transport(*args,**kwargs):
            if args[3].startswith('/outputs/') and args[2]=='GET':raise OSError('export connection interrupted')
            return await request(*args,**kwargs)
        native.request=transport
        await tools.faxes.refresh(run)
        review=(await tools.faxes.submit(run,'A','原本照合済み'))['review']
        await tools.faxes.decide(run,'A',review['id'],True,'承認')
        for command in ('first-complete','retry-complete'):
            with pytest.raises(OSError):await tools.execute(run['id'],0,'complete_fax_work',{},command)
            assert len(native.writes)==5
        # An unknown export GET does not authorize repeating the confirmed writes.
        assert native.wfs['OA']['state']=='confirmed'
    asyncio.run(scenario())


def test_stop_during_helper_prevents_the_next_native_write(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        entered=asyncio.Event();release=asyncio.Event();request=native.request
        async def delayed(*args,**kwargs):
            if args[3]=='/rule-preflight':
                entered.set();await release.wait()
            return await request(*args,**kwargs)
        native.request=delayed
        task=asyncio.create_task(tools.execute(run['id'],0,'prepare_shift_review',{},'long-helper'))
        await asyncio.wait_for(entered.wait(),1)
        store.fence(run['id']);release.set()
        with pytest.raises(Conflict):await task
        assert [path for path,_ in native.writes]==['/rule-preflight']
        assert not tools.flow.review(run['id']) and store.get(run['id'])['status']!='completed'
    asyncio.run(scenario())


def test_fax_completion_verifies_each_started_upload_after_approval(tmp_path):
    import httpx
    from experiments.agent_assistant.tests.test_fax_workflow import Native
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.tools import Tools
    async def scenario():
        store=Store(tmp_path/'ledger');store.set_budget(100)
        run,_=store.create('user','pending-completion','FAX',['hospital'],100,task_id='hospital_pending_faxes')
        store.update(run['id'],status='running');run=store.get(run['id'])
        native=Native(tmp_path/'files');request=native.request
        async def exporting(run,domain,method,path,**kwargs):
            if path.startswith('/outputs/') and method=='GET':
                return httpx.Response(200,content=b'synthetic sealed output',headers={'content-type':'text/csv'})
            return await request(run,domain,method,path,**kwargs)
        native.request=exporting;tools=Tools(store,native)
        for fax in ('A','B'):
            native.sources[fax]['status']='completed'
            # A real start write must be followed by a read of this exact upload.
            store.command(run['id'],'start-'+fax,{'name':'hospital_start_fax','arguments':{'parameters':{'ids':{'uploaded_pdf_id':fax}}}})
            store.finish_command(run['id'],'start-'+fax,{'ok':True,'data':native.sources[fax]})
            await tools.execute(run['id'],0,'prepare_fax_review',{'fax_id':fax},'prepare-'+fax)
            review=(await tools.faxes.submit(run,fax,'合成原本の照合済み'))['review']
            await tools.faxes.decide(run,fax,review['id'],True,'承認')
        result=await tools.execute(run['id'],0,'complete_fax_work',{},'complete')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed'
        evidence=next(e['payload'] for e in store.events(run['id']) if e['kind']=='completion_evidence')
        with store.tx() as db:
            reads=[json.loads(db.execute('select request from commands where id=?',(ident,)).fetchone()['request']) for ident in evidence['evidence_commands']]
        assert {r['arguments']['parameters']['ids']['uploaded_pdf_id'] for r in reads if r['name']=='hospital_get_fax'}=={'A','B'}
    asyncio.run(scenario())
