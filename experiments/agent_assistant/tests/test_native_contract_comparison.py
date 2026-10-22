import json
from experiments.agent_assistant import check_native_contracts as checker


def test_main_routes_flat_schemas_and_removed_modules_are_compared(tmp_path, monkeypatch):
    bundle=tmp_path/'native/shift';(bundle/'backend/shift_scheduler_backend').mkdir(parents=True)
    (tmp_path/'manifests').mkdir()
    (tmp_path/'manifests/shift.json').write_text(json.dumps({'source_revision':'old'}))
    (bundle/'backend/shift_scheduler_backend/main.py').write_text('@app.get("/old")\ndef old(): pass\n')
    (bundle/'backend/shift_scheduler_backend/deleted_api.py').write_text('@router.get("/gone")\ndef gone(): pass\n')
    content={'backend/shift_scheduler_backend/main.py':b'@app.get("/new")\ndef new(): pass\n',
             'backend/shift_scheduler_backend/planning_api_schemas.py':b'class Input(BaseModel):\n    value: int\n'}
    def git(_repo,*args):
        if args[0]=='rev-parse':return b'newsha\n'
        if args[0]=='ls-tree':return '\n'.join(content).encode()
        return content[args[1].split(':',1)[1]]
    monkeypatch.setattr(checker,'git',git)
    result=checker.compare(bundle,tmp_path,'develop')
    assert result['compared_files']==3 and result['current_route_handlers']==1
    assert result['missing_contract_files']==['backend/shift_scheduler_backend/planning_api_schemas.py']
    assert {row['declaration'] for row in result['changed_declarations']}=={'route:old','route:new','route:gone'}
    assert not result['source_contract_equal']


def test_router_prefix_and_mount_changes_are_not_hidden():
    before=checker.declarations(b'router=APIRouter(prefix="/v1")\napp.include_router(router,prefix="/api")')
    after=checker.declarations(b'router=APIRouter(prefix="/v2")\napp.include_router(router,prefix="/new")')
    assert before!=after
    assert 'router:router' in before and any(k.startswith('mount:') for k in before)
