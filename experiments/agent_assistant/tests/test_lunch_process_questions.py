"""Question identity, registered choices and saved graph fences, synthetic transport."""
import asyncio
from copy import deepcopy
import pytest
from experiments.agent_assistant.tests.test_selected_tasks import lunch, QUERY
from experiments.agent_assistant.tests.daily_question_fixture import install, complete, reply
from experiments.agent_assistant.tools import Arguments
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.conversation import pending_question


def setup(tmp_path):
    store,native,tools,run=lunch(tmp_path,'lunch_daily_bundle')
    context=run['task_inputs'];context['inputs']['service_date']='2026-11-02'
    store.update(run['id'],task_inputs=context);run=store.get(run['id']);install(native)
    return store,native,tools,run


def test_questions_cover_materials_shared_step_and_exact_saved_selection(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        result=await tools.execute(run['id'],0,'lunch_create_schedule',{'parameters':{'query':QUERY,'reason':'合成試験'}},'premature')
        assert '質問' in result['error'];assert not native.writes
        state=await complete(tools,run)
        process=native.data['/api/school-lunch/menu-executions/101/process']['document']
        assert [s['equipment']['id'] for s in process['steps']]==['table-1','table-2','kettle-b']
        assert len([e for e in store.events(run['id']) if e['kind']=='process_answer'])==4
        questions=[e['payload']['result']['question'] for e in store.events(run['id']) if e['kind']=='tool_result' and e['payload']['tool']=='ask_lunch_process_question' and e['payload']['result'].get('waiting_input')]
        assert 'にんじん' in questions[0] and 'じゃがいも' in questions[1]
        assert 'にんじん・じゃがいも' in questions[2] and '一緒に調理' in questions[2]
        assert 'にんじん・じゃがいも' in questions[3] and 'どの釜' in questions[3]
        result=await tools.execute(run['id'],0,'lunch_create_schedule',{'parameters':{'query':QUERY,'reason':'全回答後'}},'after-answers')
        assert result.get('ok'),result
        await tools.flow.questions.gate(run,'review')
        assert state['complete']
    asyncio.run(scenario())


def test_model_cannot_forge_answer_save_or_skip_unanswered_steps(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'question')
        with pytest.raises(Conflict,match='登録済み'):await tools.flow.questions.validate_reply(run,'未登録作業台','question')
        with pytest.raises(Conflict,match='最新'):await tools.flow.questions.validate_reply(run,q['options'][0],'old-question')
        await reply(tools,run,q,q['options'][0])
        with pytest.raises(Conflict,match='未回答'):await tools.flow.questions.gate(run,'review')
        assert not native.writes
        store.fence(run['id'])
        with pytest.raises(Conflict):await tools.execute(run['id'],0,'ask_lunch_process_question',{},'late')
        assert not native.writes
    asyncio.run(scenario())


def test_bound_answer_save_uses_server_payload_and_is_idempotent(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        rejected=await tools.execute(run['id'],0,'save_lunch_process_answers',{},'premature-save')
        assert rejected.get('error') and not native.writes
        for i,selection in enumerate(['下処理台1','下処理台2','一緒に調理しました','回転釜B']):
            q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'q'+str(i))
            await reply(tools,run,q,selection)
        saved=await tools.execute(run['id'],0,'save_lunch_process_answers',{},'save-answers')
        assert saved['ok'] and len(native.writes)==1
        assert await tools.execute(run['id'],0,'save_lunch_process_answers',{},'save-answers')==saved
        assert len(native.writes)==1
        assert [s['equipment']['id'] for s in native.data['/api/school-lunch/menu-executions/101/process']['document']['steps']]==['table-1','table-2','kettle-b']
        store.fence(run['id'])
        with pytest.raises(Conflict):await tools.execute(run['id'],0,'save_lunch_process_answers',{},'late-save')
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['menu','equipment','layout','process'])
def test_changed_source_rejects_old_answer(tmp_path,change):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'question')
        if change=='menu':native.data['/api/school-lunch/menu-executions'][0]['version']+=1
        if change=='equipment':native.data['/api/school-lunch/menu-executions/101/process/route-options']['templates'][0]['equipment'][0]['name']='変更された台'
        if change=='layout':native.data['/api/school-lunch/route-diagrams/layouts'][0]['locations']['下処理台1']=[.7,.8]
        if change=='process':native.data['/api/school-lunch/menu-executions/101/process']['document']['steps'][0]['operation']='変更された工程'
        with pytest.raises(Conflict):await tools.flow.questions.validate_reply(run,q['options'][0],'question')
        assert not native.writes
    asyncio.run(scenario())


def test_no_candidates_or_missing_graph_never_selects_default(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        native.data['/api/school-lunch/menu-executions/101/process/route-options']['templates'][0]['equipment']=[]
        result=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'no-place')
        assert '登録済み' in result['error'] and not native.writes
        native.data['/api/school-lunch/menu-executions/101/process']['document']['steps']=[]
        result=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'no-graph')
        assert '工程案' in result['error'] and not native.writes
    asyncio.run(scenario())


def test_saving_different_equipment_or_changed_graph_is_rejected(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        for i in range(4):
            q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'q'+str(i));await reply(tools,run,q,q['options'][0])
        ready=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'ready')
        args=deepcopy(ready['process_saves'][0]);args['body']['document']['steps'][-1]['equipment']['id']='kettle-b'
        result=await tools.execute(run['id'],0,'lunch_save_process',{'parameters':args},'forged-save')
        assert '一致' in result['error'];assert not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize("send_fails", [False, True])
def test_real_managed_reply_binds_exact_question_and_rejects_stop_during_send(tmp_path, send_fails):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        managed=Managed(store,native,tools,None,{})
        async def follow(ident,event):pass
        managed.follow=follow
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'managed-question')
        store.event(run['id'],'operator_wait',{'command_id':q['command_id'],'result':q})
        await managed.reply(run['id'],q['options'][0],q['command_id'],q['options'][0])
        assert len(tools.flow.questions.state(run)['documents'][0]['answers'])==1
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'stop-question')
        store.event(run['id'],'operator_wait',{'command_id':q['command_id'],'result':q})
        async def stop_during_send(ident,event):
            store.fence(ident)
            if send_fails: raise Conflict('停止中の送信失敗')
        managed.follow=stop_during_send
        with pytest.raises(Conflict,match='停止'):await managed.reply(run['id'],q['options'][0],q['command_id'],q['options'][0])
        assert len(tools.flow.questions.state(run)['documents'][0]['answers'])==1
        assert store.get(run['id'])['status']=='stopping'
    asyncio.run(scenario())


def test_all_registered_choices_visible_including_ninth_and_ambiguous_names(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        template=native.data['/api/school-lunch/menu-executions/101/process/route-options']['templates'][0]
        template['equipment']=[{'id':'table-'+str(i),'name':'作業台'+str(i),'type':'作業台','ports':[]} for i in range(10)]
        locations=native.data['/api/school-lunch/route-diagrams/layouts'][0]['locations']
        locations.update({x['name']:[.2,.3] for x in template['equipment']})
        native.data['/api/school-lunch/menu-executions/101/process/question-drawing']['equipment']=[{**x,'bounds':[10+i*70,100,60,80]} for i,x in enumerate(template['equipment'])]
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'many-choices')
        pending=pending_question(store.events(run['id']))
        assert len(pending['options'])==10 and pending['options'][9]=='作業台9'
        selected=await tools.flow.questions.validate_reply(run,'作業台9',q['command_id'])
        assert selected['value']['id']=='table-9'
    asyncio.run(scenario())


def test_stop_while_loading_question_sources_cannot_resurrect_waiting_state(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        request=native.request
        async def stop_in_get(run,domain,method,path,**kwargs):
            response=await request(run,domain,method,path,**kwargs)
            if path.endswith('/route-options'):store.fence(run['id'])
            return response
        native.request=stop_in_get
        result=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'stop-in-get')
        assert '停止' in result['error']
        assert store.get(run['id'])['status']!='waiting_input'
        assert tools.flow.questions.state(run) is None
    asyncio.run(scenario())


def test_original_drawing_geometry_and_hash_are_bound_to_question(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'mapped-question')
        picture=native.data['/api/school-lunch/menu-executions/101/process/question-drawing']
        assert q['question_map']['sha256']==picture['sha256']
        assert q['question_map']['hotspots'][0]['bounds']==picture['equipment'][0]['bounds']
        assert 'svg_base64' not in q['question_map']
        picture['equipment'][0]['bounds'][0]+=1
        with pytest.raises(Conflict,match='原本図面'):await tools.flow.questions.validate_reply(run,'下処理台1','mapped-question')
        assert not native.writes
        store.update(run['id'],status='running')  # A resumed provider explicitly requests fresh questions.
        restarted=await tools.execute(run['id'],0,'ask_lunch_process_question',{'restart':True},'restarted-map')
        assert restarted.get('waiting_input'),restarted
        assert restarted['question_map']['hotspots'][0]['bounds'][0]==picture['equipment'][0]['bounds'][0]
    asyncio.run(scenario())


def test_unknown_equipment_on_original_blocks_question(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        native.data['/api/school-lunch/menu-executions/101/process/question-drawing']['equipment']=[]
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'missing-map')
        assert not q.get('waiting_input') and q.get('error')
        assert not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['一緒に調理しました','別々に調理しました'])
def test_grouping_answer_controls_generic_ingredient_place_questions_and_exact_save(tmp_path,mode):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        # Three unrelated names and two shared stages prove the mechanism is data driven.
        graph=native.data['/api/school-lunch/menu-executions/101/process']['document']
        graph['ingredients']=[{'id':'a','name':'キャベツ','state':''},{'id':'b','name':'豚肉','state':''},{'id':'c','name':'豆腐','state':''}]
        graph['steps']=[{'id':'wash','operation':'洗う','inputs':[{'ingredient_id':m['id']} for m in graph['ingredients']],'equipment':{'type':'作業台','id':None},'conditions':{},'basis':{},'outputs':[]},
                        {'id':'heat','operation':'加熱する','inputs':[{'step_id':'wash'}],'equipment':{'type':'回転釜','id':None},'conditions':{},'basis':{},'outputs':[]}]
        before=deepcopy(graph)
        async def ask(index):return await tools.execute(run['id'],0,'ask_lunch_process_question',{},'generic-'+str(index))
        q=await ask(0)
        assert q['process_context']['kind']=='grouping' and not q.get('question_map')
        assert all(m['name'] in q['question'] for m in graph['ingredients'])
        with pytest.raises(Conflict):await tools.flow.questions.validate_reply(run,'下処理台1',q['command_id'])
        await reply(tools,run,q,mode)
        count=1 if mode=='一緒に調理しました' else 3
        for index in range(count):
            q=await ask(index+1)
            if count==3:
                assert before['ingredients'][index]['name'] in q['question']
                assert all(m['name'] not in q['question'] for m in before['ingredients'] if m['id']!=before['ingredients'][index]['id'])
            assert q.get('question_map')
            await reply(tools,run,q,'下処理台1' if index%2==0 else '下処理台2')
        q=await ask(10)
        assert q['process_context']['kind']=='grouping'  # Later common work needs its own answer.
        await reply(tools,run,q,'別々に調理しました')
        for index,name in enumerate(['キャベツ','豚肉','豆腐']):
            q=await ask(11+index)
            assert name in q['question'] and 'どの釜' in q['question']
            if index==2:
                with pytest.raises(Conflict,match='未回答'):await tools.flow.questions.gate(run,'review')
            await reply(tools,run,q,'回転釜A' if index!=1 else '回転釜B')
        ready=await ask(20);assert ready.get('complete'),ready
        document=ready['process_saves'][0]['body']['document']
        assert document['ingredients']==before['ingredients']
        assert [(s['inputs'],s['outputs'],s['conditions']) for s in document['steps']]==[(s['inputs'],s['outputs'],s['conditions']) for s in before['steps']]
        assert document['steps'][1]['equipment']['id'] is None
        assert [p['equipment']['id'] for p in document['steps'][1]['daily_work']['placements']]==['kettle-a','kettle-b','kettle-a']
        assert document['steps'][0]['daily_work']['mode']==('together' if count==1 else 'separate')
        saved=await tools.execute(run['id'],0,'lunch_save_process',{'parameters':ready['process_saves'][0]},'generic-save')
        assert saved.get('ok'),saved
    asyncio.run(scenario())


def test_grouping_question_is_stale_after_source_change_and_cannot_answer_after_stop(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        for index in range(2):
            q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'prep-'+str(index));await reply(tools,run,q,q['options'][0])
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'shared')
        assert q['process_context']['kind']=='grouping'
        native.data['/api/school-lunch/menu-executions/101/process']['version']+=1
        with pytest.raises(Conflict):await tools.flow.questions.validate_reply(run,'一緒に調理しました','shared')
        native.data['/api/school-lunch/menu-executions/101/process']['version']-=1
        selected=await tools.flow.questions.validate_reply(run,'別々に調理しました','shared')
        store.fence(run['id'])
        with pytest.raises(Conflict):tools.flow.questions.accept_reply(run,selected)
        assert 'cook' not in tools.flow.questions.state(run)['documents'][0]['work_groups']
        assert not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize('legacy_locations',[{}, {'旧台': [.1,.2]}])
def test_canonical_drawing_equipment_does_not_require_legacy_layout_points(tmp_path,legacy_locations):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        native.data['/api/school-lunch/route-diagrams/layouts'][0]['locations']=legacy_locations
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'canonical-question')
        assert q.get('waiting_input'),q
        assert '下処理台1' in q['options'] and '旧台' not in q['options']
        assert q.get('question_map',{}).get('hotspots'),q
        for index,label in enumerate(['下処理台1','下処理台2','一緒に調理しました','回転釜B']):
            if index:q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'canonical-q'+str(index))
            await reply(tools,run,q,label)
        state=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'canonical-ready')
        assert state['complete']
        for index,parameters in enumerate(state['process_saves']):
            saved=await tools.execute(run['id'],0,'lunch_save_process',{'parameters':parameters},'canonical-save'+str(index))
            assert saved.get('ok'),saved
        process=native.data['/api/school-lunch/menu-executions/101/process']['document']
        assert process['steps'][0]['equipment']['id']=='table-1'
    asyncio.run(scenario())


def test_template_question_uses_verified_original_title_instead_of_internal_id(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        native.data['/api/school-lunch/menu-executions/101/process']['document']['route_config']=None
        native.data['/api/school-lunch/menu-executions/101/process/question-drawing']['title']='厨房基本図面 — ご飯用'
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'drawing-choice')
        assert q.get('waiting_input') and q['options']==['厨房基本図面 — ご飯用'],q
        await reply(tools,run,q,q['options'][0])
        next_question=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'equipment-choice')
        assert next_question['question_map']['template_id']=='kitchen-rice'
        assert not native.writes
    asyncio.run(scenario())


def test_original_drawing_version_must_match_equipment_options(tmp_path):
    async def scenario():
        _,native,tools,run=setup(tmp_path)
        native.data['/api/school-lunch/menu-executions/101/process/question-drawing']['template_version']=2
        q=await tools.execute(run['id'],0,'ask_lunch_process_question',{},'changed-drawing-version')
        assert '版が一致しません' in q['error'] and not native.writes
    asyncio.run(scenario())
