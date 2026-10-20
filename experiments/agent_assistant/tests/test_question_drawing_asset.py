"""Source image access is bound to authenticated, current operator questions."""
import hashlib
from fastapi.testclient import TestClient
from experiments.agent_assistant.app import create_app


def test_asset_requires_current_question_and_untampered_source(tmp_path):
    class Client:
        async def close(self): pass
    app=create_app({'domains':{}},tmp_path/'web',managed_client=Client())
    store=app.state.store;store.set_budget(1000)
    run,_=store.create('local-trial-operator','drawing-test-key','合成質問',['school-lunch'],100)
    store.update(run['id'],status='waiting_input')
    raw=b'<svg xmlns="http://www.w3.org/2000/svg"/>';digest=hashlib.sha256(raw).hexdigest()
    folder=app.state.sandbox.path(run['id'])/'question-drawings';folder.mkdir(parents=True)
    asset=folder/(digest+'.svg');asset.write_bytes(raw)
    store.event(run['id'],'tool_result',{'tool':'ask_lunch_process_question','result':{'question':'場所は？','command_id':'q1','question_map':{'sha256':digest}}})
    url=f"/runs/{run['id']}/question-drawings/{digest}?question_id=q1"
    with TestClient(app) as client:
        assert client.get(url).status_code==401
        headers={'X-Trial-Token':app.state.token}
        response=client.get(url,headers=headers)
        assert response.status_code==200 and response.content==raw
        assert response.headers['cache-control']=='no-store'
        assert client.get(url.replace('q1','old'),headers=headers).status_code==409
        asset.write_bytes(b'changed')
        assert client.get(url,headers=headers).status_code==409
        asset.write_bytes(raw);store.update(run['id'],status='stopped')
        assert client.get(url,headers=headers).status_code==409
        store.update(run['id'],status='rolled_back')
        assert client.get(url,headers=headers).status_code==409
