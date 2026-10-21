"""Canonical map identity survives the editable native export bridge."""
import asyncio
from copy import deepcopy
import pytest
from experiments.agent_assistant.tests.test_daily_completion import setup
from experiments.agent_assistant.tests.daily_question_fixture import complete
from experiments.agent_assistant.canonical_route_layout import prepare
from experiments.agent_assistant.store import Conflict


def test_map_bridge_preserves_registered_original_and_is_idempotent(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        originals=deepcopy(native.data['/api/school-lunch/route-diagrams/layouts'])
        result=await prepare(tools,store.get(run['id']))
        receipt=result['task_inputs']['canonical_route_layout']
        assert receipt['id'] not in [r['id'] for r in originals]
        assert native.data['/api/school-lunch/route-diagrams/layouts'][:len(originals)]==originals
        assert receipt['binding']['locations']['下処理台1']==[150/900,140/600]
        assert len(receipt['binding']['svg_sha256'])==64
        count=len(native.writes)
        again=await prepare(tools,result)
        assert again['task_inputs']['canonical_route_layout']==receipt and len(native.writes)==count
        await tools.flow.questions.gate(again,'review')
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['image','locations','version','school'])
def test_modified_output_map_cannot_replace_answered_original(tmp_path,change):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        result=await prepare(tools,store.get(run['id']))
        row=next(r for r in native.data['/api/school-lunch/route-diagrams/layouts'] if r['id']==result['task_inputs']['inputs']['layout_id'])
        if change=='image':row['image']='data:image/png;base64,ZmFrZQ=='
        elif change=='locations':row['locations']['下処理台1']=[.9,.9]
        elif change=='version':row['version']+=1
        else:row['facility_id']=99
        count=len(native.writes)
        with pytest.raises((Conflict,ValueError,RuntimeError)):await prepare(tools,result)
        assert len(native.writes)==count
    asyncio.run(scenario())


def test_unknown_map_creation_outcome_and_stopped_run_are_not_replayed(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        request=native.request;creates=0
        async def lost(*args,**kwargs):
            nonlocal creates
            response=await request(*args,**kwargs)
            if args[2]=='POST' and args[3]=='/api/school-lunch/route-diagrams/layouts':
                creates+=1;raise TimeoutError('synthetic response lost after creation')
            return response
        native.request=lost
        with pytest.raises(TimeoutError):await prepare(tools,store.get(run['id']))
        with pytest.raises(Conflict,match='未確定'):await prepare(tools,store.get(run['id']))
        assert creates==1
        store.fence(run['id'])
        with pytest.raises(Conflict,match='停止'):await prepare(tools,run)
        assert creates==1
    asyncio.run(scenario())


def test_monthly_neighbor_errors_do_not_change_selected_day_evidence(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        for path in ('/api/school-lunch/menu-executions','/api/school-lunch/work-schedules','/api/school-lunch/route-diagrams'):
            sibling=deepcopy(native.data[path][0]);sibling.update(id=999,service_date='2026-11-03',stale=True,blockers=['別日の未解決'])
            native.data[path].append(sibling)
        result=await tools.execute(run['id'],0,'prepare_lunch_daily_review',{},'selected-day')
        assert result['ok'],result
        review=tools.flow.review(run['id'])
        for entry in review['evidence']:
            if entry['operation'] in ('lunch_get_menus','lunch_get_schedules','lunch_get_routes'):
                assert all(row['service_date']=='2026-11-02' for row in entry['data'])
        row=native.data['/api/school-lunch/work-schedules'][0];row['stale']=True
        with pytest.raises(Conflict):await tools.flow.recheck(store.get(run['id']),review)
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['sha256','template_version','bounds'])
def test_same_template_name_with_different_menu_drawing_never_creates_map(tmp_path,monkeypatch,change):
    async def scenario():
        store,native,tools,run=setup(tmp_path);await complete(tools,run)
        current=store.get(run['id']);state=await tools.flow.questions.checked(current)
        other=deepcopy(state['documents'][0]);other['menu_id']=102
        state['documents'].append(other)
        async def checked(_):return deepcopy(state)
        original=tools.flow.questions.drawing
        async def drawing(actual,menu_id,template):
            result=deepcopy(await original(actual,101,template))
            if menu_id==102:
                if change=='bounds':result['equipment'][0]['bounds'][0]+=1
                elif change=='sha256':result['sha256']='f'*64
                else:result['template_version']+=1
            return result
        monkeypatch.setattr(tools.flow.questions,'checked',checked)
        monkeypatch.setattr(tools.flow.questions,'drawing',drawing)
        count=len(native.writes)
        with pytest.raises(Conflict,match='一致しません'):await prepare(tools,current)
        assert len(native.writes)==count
    asyncio.run(scenario())
