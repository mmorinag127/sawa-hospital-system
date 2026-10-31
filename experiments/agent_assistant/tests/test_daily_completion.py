import asyncio
from copy import deepcopy
import pytest
from experiments.agent_assistant.tests.test_daily_lunch import setup as base_setup,PRICE_PATH
from experiments.agent_assistant.tests.daily_question_fixture import complete
from experiments.agent_assistant.tests.test_selected_tasks import record,QUERY
from experiments.agent_assistant.store import Conflict

def setup(tmp_path):
    store,native,tools,run=base_setup(tmp_path)
    native.data['/api/school-lunch/work-schedules'][0]['timeline_ticks']=['07:30','08:00','08:30','08:50','09:20','09:50','10:20','10:40']
    return store,native,tools,run


def test_server_builds_evidence_and_export_versions_only_after_human_approval(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        await complete(tools,run)
        denied=await tools.execute(run['id'],0,'complete_lunch_daily_work',{},'premature')
        assert '承認' in denied['error']
        prepared=await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'review')
        assert prepared['ok'] and prepared['queued_for_human']
        review=tools.flow.review(run['id']);assert not review['actions']
        assert {e['operation'] for e in review['evidence']} >= {'lunch_get_process','lunch_get_menus','lunch_get_schedules','lunch_get_routes','lunch_get_price_source','lunch_get_price_result'}
        await tools.flow.decide(store.get(run['id']),review['id'],True,'試験データを承認')
        result=await tools.execute(run['id'],0,'complete_lunch_daily_work',{},'complete')
        assert result['ok'],result
        assert store.get(run['id'])['status']=='completed'
        commands=tools.flow.commands(run['id'])
        exports=[c for c in commands if c['request']['name'] in ('lunch_export_schedule','lunch_export_route','lunch_export_price')]
        assert len(exports)==3 and all(c['result'].get('artifact') for c in exports)
        school = run['task_inputs']['targets'][0]['data']['facility']['name']
        assert all(c['request']['arguments']['parameters']['query']['facility_name'] == school for c in exports)
        assert next(c for c in exports if c['request']['name']=='lunch_export_schedule')['request']['arguments']['parameters']['query']['format']=='original'
        assert next(c for c in exports if c['request']['name']=='lunch_export_price')['request']['arguments']['parameters']['query']['expected_fingerprint']=='b'*64
    asyncio.run(scenario())


def test_changed_approved_result_blocks_every_export(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'review')
        review=tools.flow.review(run['id']);await tools.flow.decide(store.get(run['id']),review['id'],True,'承認')
        native.data[PRICE_PATH+'/preview']['fingerprint']='c'*64
        result=await tools.execute(run['id'],0,'complete_lunch_daily_work',{},'changed')
        assert result.get('error')
        assert not any(c['request']['name'].startswith('lunch_export_') for c in tools.flow.commands(run['id']))
    asyncio.run(scenario())


def test_ambiguous_price_and_stopped_request_do_not_pick_a_candidate(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        record(tools,run,'lunch_save_price_source',{'draft_id':'c'*32},{'query':QUERY,'body':deepcopy(native.data[PRICE_PATH])})
        result=await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'ambiguous')
        assert '一つ' in result['error'] and not tools.flow.review(run['id'])
        store.fence(run['id'])
        with pytest.raises(Conflict):await tools.execute(run['id'],0,'complete_lunch_daily_work',{},'late')
    asyncio.run(scenario())


def test_original_axis_missing_is_reported_before_human_approval(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        native.data['/api/school-lunch/work-schedules'][0]['timeline_ticks']=None
        result=await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'missing-axis')
        assert '8個の時刻目盛' in result['error'] and not tools.flow.review(run['id'])
    asyncio.run(scenario())


@pytest.mark.parametrize('costs',[[{'status':'確認済み'}],None,['confirmed']])
def test_invalid_confirmation_encoding_never_saves_an_unusable_price(tmp_path,costs):
    async def scenario():
        store,native,tools,run=setup(tmp_path);before=len(native.writes)
        result=await tools.execute(run['id'],0,'lunch_save_price_source',{'parameters':{'query':QUERY,'body':{'cost_items':costs},'reason':'試験'}},'invalid-status')
        assert 'confirmed' in result['error'] and len(native.writes)==before
    asyncio.run(scenario())


@pytest.mark.parametrize('tampered',[False,True])
def test_corrected_price_is_bound_to_its_successful_native_save(tmp_path,tampered):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        corrected='c'*32
        payload=deepcopy(native.data[PRICE_PATH])
        native.data[PRICE_PATH.replace('a'*32,corrected)]=payload
        native.data[PRICE_PATH.replace('a'*32,corrected)+'/preview']={**deepcopy(native.data[PRICE_PATH+'/preview']),'draft_id':corrected}
        command=record(tools,run,'lunch_save_price_source',{'draft_id':corrected},{'query':QUERY,'body':payload})
        store.event(run['id'],'daily_price_binding',{'command_id':command,'draft_id':'d'*32 if tampered else corrected})
        result=await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'bound-review')
        if tampered:
            assert '証跡' in result['error'] and not tools.flow.review(run['id'])
        else:
            assert result['ok'],result
            evidence=tools.flow.review(run['id'])['evidence']
            assert {entry['parameters']['ids']['draft_id'] for entry in evidence if entry['operation'] in ('lunch_get_price_source','lunch_get_price_result')}=={corrected}
    asyncio.run(scenario())


def test_price_save_records_the_native_selection_instead_of_model_authored_id(tmp_path):
    import httpx
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        request=native.request
        async def save(*args,**kwargs):
            if args[2]=='POST' and args[3]=='/implementation-price-tables/source-drafts':return httpx.Response(200,json={'draft_id':'c'*32})
            return await request(*args,**kwargs)
        native.request=save
        result=await tools.execute(run['id'],0,'lunch_save_price_source',{'parameters':{'query':QUERY,'body':deepcopy(native.data[PRICE_PATH]),'reason':'回答に基づく価格の訂正'}},'corrected-save')
        assert result['ok'],result
        assert [event['payload'] for event in store.events(run['id']) if event['kind']=='daily_price_binding']==[{'command_id':'corrected-save','draft_id':'c'*32}]
    asyncio.run(scenario())


def test_duplicate_tax_representations_are_rejected_before_saving(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        body={**deepcopy(native.data[PRICE_PATH]),'cost_items':[{'status':'confirmed','unit_price_tax_included':200,'unit_price_tax_excluded':200}]}
        before=len(native.writes)
        result=await tools.execute(run['id'],0,'lunch_save_price_source',{'parameters':{'query':QUERY,'body':body,'reason':'試験'}},'duplicate-price')
        assert '片方だけ' in result['error'] and len(native.writes)==before
        assert store.get(run['id'])['status']=='running'
        assert not any(event['kind']=='daily_price_binding' for event in store.events(run['id']))
    asyncio.run(scenario())


def test_known_price_format_error_returns_to_model_without_asking_operator(tmp_path):
    import httpx
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        request=native.request
        async def rejected(*args,**kwargs):
            if args[3]==PRICE_PATH+'/preview':return httpx.Response(400,json={'detail':'schools[1].items[1] must not define both tax-included and tax-excluded prices'})
            return await request(*args,**kwargs)
        native.request=rejected
        result=await tools.execute(run['id'],0,'lunch_get_price_result',{'parameters':{'ids':{'draft_id':'a'*32},'query':QUERY}},'price-format')
        assert result['input_validation_failed'] and 'needs_operator_confirmation' not in result
        assert store.get(run['id'])['status']=='running'
    asyncio.run(scenario())
