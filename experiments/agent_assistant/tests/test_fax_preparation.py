"""Fixed preparation with native fixtures and a simulated provider, not paid OCR."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from experiments.agent_assistant.business_contracts import model_definitions, require_server
from experiments.agent_assistant.fax_preparation import prepare, content as packet_content
from experiments.agent_assistant.gemini import Gemini, content
from experiments.agent_assistant.mcp_server import build_mcp
from experiments.agent_assistant.store import Store, Conflict
from experiments.agent_assistant.tools import Tools
from experiments.agent_assistant.tests.test_fax_workflow import Native
from experiments.agent_assistant.tests.test_gemini import interaction


def setup(tmp_path):
    import fitz
    native = Native(tmp_path/'native')
    native.configs = {}
    with fitz.open() as pdf:
        pdf.new_page().insert_text((20, 30), 'Synthetic FAX')
        native.pdf = pdf.tobytes()
    for source in native.sources.values():
        source.update(status='completed', content_sha256=hashlib.sha256(native.pdf).hexdigest())
    store = Store(tmp_path/'ledger');store.set_budget(100)
    run,_ = store.create('user','fixed-fax-prepare','FAX',['hospital'],25,task_id='hospital_pending_faxes',
        task_inputs={'business_workflow':{'version':1,'fax_harness':2,'phase':'preparing'}})
    store.update(run['id'],status='running')
    tools = Tools(store,native)
    calls = []
    async def call(run,name,raw,command):
        calls.append((name,raw,command))
        if name == 'hospital_start_fax':
            native.sources[raw['parameters']['ids']['uploaded_pdf_id']]['status']='completed'
        return {'ok':True}
    return store,native,tools,store.get(run['id']),SimpleNamespace(tools=tools,call=call),calls


def test_prepared_packet_has_exact_sheets_and_all_pages_without_model_fetches(tmp_path):
    async def scenario():
        store,native,tools,run,engine,calls=setup(tmp_path)
        native.sources['A']['status']='pending'
        native.sheets['OA']['cell_provenance_rows']=['retained-only-in-native-ledger' * 100000]
        assert await prepare(engine,run)
        count=len(calls)
        assert await prepare(engine,run) and len(calls)==count
        blocks=await packet_content(tools,run)
        assert len([block for block in blocks if block['type']=='image'])==2
        sheets=[json.loads(block['text']) for block in blocks[1:] if block['type']=='text']
        assert [item['sheet'] for item in sheets]==[{key: sheet[key] for key in ('header','fields','rows')} for sheet in native.sheets.values()]
        assert len(json.dumps(sheets)) < 10000
        assert 'cell_provenance_rows' in native.sheets['OA']
        assert sum(name=='hospital_start_fax' for name,_,_ in calls)==1
        assert not native.writes
        assert {item['name'] for item in model_definitions(tools,run)}=={'request_fax_approval','ask_operator'}
        for name in ('hospital_select_ocr','fax_work_queue','read_tool_result','wait_for_native_idle'):
            with pytest.raises(Conflict,match='サーバー'):
                require_server(run,name)
        native.sheets['OA']['rows'][0][1]='operator-edit'
        with pytest.raises(Conflict,match='変更'):
            await packet_content(tools,run)
    asyncio.run(scenario())


def test_ambiguous_ocr_candidates_are_not_selected(tmp_path):
    async def scenario():
        store,native,tools,run,engine,calls=setup(tmp_path)
        native.wfs['OA']['selected_ocr_result_id']=None
        original=native.request
        async def request(run,domain,method,path,**kwargs):
            if path.endswith('/ocr-results'):
                return httpx.Response(200,json={'results':[{'order_id':'OA','selectable':True,'ocr_result_id':key} for key in ('one','two')]})
            return await original(run,domain,method,path,**kwargs)
        native.request=request
        with pytest.raises(Conflict,match='一意'):
            await prepare(engine,run)
        assert not any(name=='hospital_select_ocr' for name,_,_ in calls)
        assert not store.events(run['id'],kinds=('fax_preparation',))
    asyncio.run(scenario())


def test_one_provider_request_submits_all_preloaded_fax_reviews(tmp_path,monkeypatch):
    async def scenario():
        store,native,tools,run,engine,calls=setup(tmp_path)
        await prepare(engine,run)
        for key,value in {'GEMINI_API_KEY':'synthetic','GEMINI_AGENT_MODEL':'gemini-test',
            'GEMINI_AGENT_INPUT_USD_PER_MILLION':'1','GEMINI_AGENT_OUTPUT_USD_PER_MILLION':'2'}.items():
            monkeypatch.setenv(key,value)
        requests=[]
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json=interaction('one-batch',[
                (key,'request_fax_approval',{'fax_id':key,'summary':'合成原本照合。数量変更なし'}) for key in ('A','B')]))
        async def pause(*args):
            return all(item['state']=='awaiting_approval' for item in tools.faxes.items(run['id']))
        tools.business=SimpleNamespace(pause_model=pause,kick_many=AsyncMock())
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent=Gemini(store,native,tools,build_mcp(tools),{},client=client)
        await agent.consume_gemini(run['id'],run['generation'],None,content(await packet_content(tools,run)))
        assert len(requests)==1
        assert requests[0]['generation_config']['thinking_level'] == 'low'
        assert {tool['name'] for tool in requests[0]['tools']}=={'request_fax_approval','ask_operator'}
        assert all(item['state']=='awaiting_approval' for item in tools.faxes.items(run['id']))
        assert all(item['review']['signature']==item['review']['native_signature'] for item in tools.faxes.items(run['id']))
        assert not native.writes
        with store.read() as db:
            results=[json.loads(row[0]) for row in db.execute("SELECT result FROM commands WHERE run_id=? AND status='done'",(run['id'],))]
        assert all('sheet' not in result and 'review' not in result for result in results)
        await client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', [None, 'revision', 'coordinate', 'schema'])
def test_native_quantity_review_retains_raw_and_exact_cell_identity(tmp_path, fault):
    async def scenario():
        store, native, tools, run, engine, calls = setup(tmp_path)
        await tools.faxes.refresh(run)
        item = tools.faxes.item(run['id'], 'A')
        captured = await tools.faxes.capture(run, item)
        captured['sheet'].update(header=['日付','区分','献立','数量'],
            fields=['date_mmdd','daypart','menu','qty.regular_x'], rows=[['11/15','昼','献立A','12_']])
        original = native.request
        issue = {'row_index': 0, 'column_index': 3, 'field': 'qty.regular_x', 'raw': '12_'}
        async def request(*args, **kwargs):
            if args[3].endswith('/pre-save-status'):
                report = {'quantity_ready': False, 'cell_issues': [{**issue, **({'column_index':99} if fault=='coordinate' else {})}]}
                if fault == 'schema': report = {'error':'quantity_template_field_unknown','quantity_ready':False,'cell_issues':[]}
                return httpx.Response(200,json={'workflow':{'revision':captured['revision'] + (1 if fault=='revision' else 0)},'materialization':report})
            return await original(*args, **kwargs)
        native.request = request
        if fault:
            with pytest.raises(Conflict): await tools.faxes.quantity_review(run, item, captured)
        else:
            report = await tools.faxes.quantity_review(run, item, captured)
            assert report == {'quantity_ready':False,'cell_issues':[{**issue,'date_mmdd':'11/15','daypart':'昼','menu':'献立A','label':'数量'}]}
            assert captured['sheet']['rows'][0][3] == '12_'
        assert not native.writes
    asyncio.run(scenario())
