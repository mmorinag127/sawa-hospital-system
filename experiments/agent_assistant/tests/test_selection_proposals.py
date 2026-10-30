"""All twelve discovery paths with owned synthetic source data, no cloud/model calls."""
import asyncio
from datetime import date
import sqlite3

import httpx
import pytest

from experiments.agent_assistant.tasks import public_tasks
from experiments.agent_assistant.selection_proposals import school_months, school_output_counts
from experiments.agent_assistant.tests.test_selected_tasks import setup, Native


class Discovery(Native):
    def prepare(self, run):
        super().prepare(run)
        if run['domains']==['school-lunch']:
            folder=self.path(run['id'])/'data/school-lunch';folder.mkdir(parents=True)
            with sqlite3.connect(folder/'database.sqlite3') as db:
                db.executescript('''
                CREATE TABLE school_lunch_menu_executions(id INTEGER,facility_id INTEGER,service_date TEXT,name TEXT);
                CREATE TABLE school_lunch_daily_work_schedules(facility_id INTEGER,service_date TEXT);
                CREATE TABLE school_lunch_route_diagrams(facility_id INTEGER,service_date TEXT);
                CREATE TABLE implementation_price_drafts(facility_id INTEGER,implementation_date TEXT);
                INSERT INTO school_lunch_menu_executions VALUES(11,8,'2026-07-01','合成カレー'),(12,8,'2026-07-01','合成サラダ'),(13,9,'2026-08-01','別校の献立');
                INSERT INTO school_lunch_daily_work_schedules VALUES(8,'2026-07-01');
                INSERT INTO school_lunch_route_diagrams VALUES(8,'2026-07-01');
                ''')

    async def request(self, run, domain, method, path, **kwargs):
        assert method=='GET', 'Discovery must never mutate business data'
        query=kwargs.get('params',{})
        if path=='/planning/period':
            from experiments.agent_assistant.selection_proposals import adjacent_month
            month=query['month']
            return httpx.Response(200,json={'target_month':month,'start_date':month+'-16','end_date_exclusive':adjacent_month(month,1)+'-16'})
        if path=='/api/school-lunch/menu-executions':
            school=query['facility_id'];month=query['month']
            values=[{'id':11,'facility_id':8,'service_date':'2026-07-01','name':'合成カレー','version':1},{'id':12,'facility_id':8,'service_date':'2026-07-01','name':'合成サラダ','version':1},{'id':13,'facility_id':9,'service_date':'2026-08-01','name':'別校の献立','version':1}]
            return httpx.Response(200,json=[item for item in values if item['facility_id']==school and item['service_date'][:7]==month])
        return await super().request(run,domain,method,path,**kwargs)


def discovery(tmp_path, monkeypatch):
    monkeypatch.setattr('experiments.agent_assistant.selection_proposals.business_today',lambda:date(2026,7,1))
    store,_,tools,_=setup(tmp_path,'shift_next_month',[])
    native=Discovery(tmp_path/'sources');tools.sandbox=native
    native.data.update({
        '/staff':{'items':[{'id':1,'name':'合成職員'}]},
        '/constraints':{'items':[{'staff_id':1,'work_date':'2026-07-02','status':'request_off'}]},
        '/solve-runs':{'items':[{'id':55,'target_month':'2026-07','confirmed':True}]},
        '/solve-runs/55':{'id':55,'target_month':'2026-07','confirmed':True,'selected_candidate_id':88,'created_at':'2026-06-25 10:00','candidates':[{'id':88}]},
        '/facilities':{'source':'canonical_json','facilities':[{'id':'F8','name':'合成病院'}]},
        '/orders':{'orders':[{'id':'private-order-id','facility':'F8','week_label':'7月第1週','week_value':'2026-07-01','received_at':'2026-06-30 10:00'}],'has_more':False},
        '/ingest/pending-faxes':{'items':[{'id':'private-fax-id','name':'合成原本.pdf','facility':'合成病院','week':'2026-07-01'}]},
        '/ingest/review-faxes':{'items':[{'id':'private-fax-id','name':'合成原本.pdf','facility':'合成病院','week':'2026-07-01','status':'needs_review'}]},
        '/api/school-lunch/facilities':[{'id':8,'name':'合成学校'},{'id':9,'name':'別の学校'}],
        '/api/school-lunch/route-diagrams/layouts':[{'id':1,'name':'合成厨房','version':1}],
    })
    return store,native,tools


@pytest.mark.parametrize('task',[item['id'] for item in public_tasks()])
def test_every_task_discovers_proposals_without_operator_calendar_input(tmp_path,monkeypatch,task):
    async def scenario():
        store,native,tools=discovery(tmp_path,monkeypatch)
        inputs={'facility_id':8} if task.startswith('lunch_') else {}
        result=await tools.flow.options(task,inputs,'operator','synthetic')
        assert result['choices']
        assert not native.writes
        if task=='hospital_pending_faxes':
            assert result['choices'][0]['label']=='合成原本.pdf ／ 合成病院 ／ 2026-07-01'
        elif task.startswith('hospital_'):
            assert result['batches'] and result['batches'][0]['keys']==['private-fax-id' if task=='hospital_review_faxes' else 'private-order-id']
            assert 'private-order-id' not in result['choices'][0]['label']
        elif task=='shift_absence':
            assert result['inputs']['month']=='2026-06'  # July 1 belongs to June 16–July 15.
            assert result['inputs']['absence_date']=='2026-07-01'
            assert len(result['absence_days'])==2
        elif task.startswith('shift_'):
            assert result['month_choices']
            if task=='shift_wishes':
                june=next(item for item in result['month_choices'] if item['month']=='2026-06')
                assert '1件' in june['reason']
            if task in ('shift_check','shift_export'):
                assert '55' not in result['choices'][0]['label'] and '88' not in result['choices'][0]['label']
        elif task=='lunch_daily_bundle':
            assert result['work_days'][0]['service_date']=='2026-07-01'
            selected=tools.flow.selection(task,'operator',result['selection_id'],['11','12'])
            assert selected['inputs']['month']=='2026-07'
        else:
            assert result['month_choices']
            july=next(item for item in result['month_choices'] if item['month']=='2026-07')
            assert '献立 2件' in july['reason'] and '作業工程表 1日分' in july['reason']
    asyncio.run(scenario())


def test_manual_month_and_date_remain_exact_and_empty_day_does_not_substitute(tmp_path,monkeypatch):
    async def scenario():
        _,_,tools=discovery(tmp_path,monkeypatch)
        result=await tools.flow.options('shift_next_month',{'month':'2026-09'},'operator','')
        assert result['inputs']['month']=='2026-09'
        assert result['choices'][0]['key']=='2026-09'
        result=await tools.flow.options('lunch_daily_bundle',{'facility_id':8,'service_date':'2026-07-20'},'operator','')
        assert result['choices']==[] and result['inputs']['service_date']=='2026-07-20'
        with pytest.raises(ValueError):tools.flow.selection('lunch_daily_bundle','operator',result['selection_id'],[])
    asyncio.run(scenario())


def test_unknown_canonical_facility_cannot_be_hidden_in_batch_proposal(tmp_path,monkeypatch):
    from experiments.agent_assistant.store import Conflict
    async def scenario():
        _,native,tools=discovery(tmp_path,monkeypatch)
        native.data['/facilities']['facilities']=[]
        with pytest.raises(Conflict,match='正本施設'):
            await tools.flow.options('hospital_outputs',{},'operator','')
        assert not native.writes
    asyncio.run(scenario())


def test_absence_selection_never_silently_ignores_a_second_staff_member(tmp_path,monkeypatch):
    async def scenario():
        _,native,tools=discovery(tmp_path,monkeypatch)
        native.data['/staff']['items'].append({'id':2,'name':'別の合成職員'})
        result=await tools.flow.options('shift_absence',{},'operator','')
        with pytest.raises(ValueError,match='一つ'):
            tools.flow.selection('shift_absence','operator',result['selection_id'],['1','2'])
        selected=tools.flow.selection('shift_absence','operator',result['selection_id'],['2'])
        assert selected['targets'][0]['key']=='2'
        assert len(selected['targets'])==1 and not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize('task,status,key',[
    ('hospital_outputs','completed','private-order-id'),
    ('hospital_review_faxes','running','private-fax-id'),
])
def test_source_selection_is_owner_bound_and_propagates_frozen_copy(tmp_path,monkeypatch,task,status,key):
    from types import SimpleNamespace
    from experiments.agent_assistant.store import Conflict
    async def scenario():
        _,native,tools=discovery(tmp_path,monkeypatch)
        source={'run_id':'a'*32,'owner':'operator','generation':0,'status':status,'manifest':{'hospital/database.sqlite3':'b'*64},'sha256':'c'*64}
        calls=[]
        def capture(ident,owner):
            calls.append((ident,owner))
            if owner!='operator':raise Conflict('owner mismatch')
            return source
        native.durable_store=SimpleNamespace(capture_copy=capture)
        result=await tools.flow.options(task,{'source_run_id':'a'*32},'operator','')
        selected=tools.flow.selection(task,'operator',result['selection_id'],[key])
        assert calls==[('a'*32,'operator')]
        assert selected['source_copy']==source and 'source_run_id' not in selected['inputs']
        with pytest.raises((ValueError,Conflict)):
            tools.flow.selection(task,'other',result['selection_id'],[key])
        with pytest.raises(Conflict):
            await tools.flow.options('hospital_review_faxes' if task=='hospital_outputs' else 'hospital_outputs',{'source_run_id':'a'*32},'operator','')
        with pytest.raises(ValueError):
            await tools.flow.options('shift_check',{'source_run_id':'a'*32},'operator','')
        assert not native.writes
    asyncio.run(scenario())
