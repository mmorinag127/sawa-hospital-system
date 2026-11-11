import json

import pytest

from experiments.agent_assistant.store import Conflict, Store, now


def make_run(store, key='request-history'):
    store.set_budget(10000)
    return store.create('operator', key, 'history', ['hospital'], 100)[0]


def test_event_cursors_keep_every_saved_event_and_current_question(tmp_path):
    store = Store(tmp_path)
    run = make_run(store)
    store.event(run['id'], 'tool_result', {'result': {'question': 'original question', 'command_id': 'question', 'options': []}})
    with store.tx() as db:
        db.executemany('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
            [(run['id'], 'notice', json.dumps({'message': str(index)}), now()) for index in range(420)])
    first = store.event_page(run['id'])
    assert len(first['events']) == 200 and first['has_previous']
    assert store.pending_question(run['id'])['question_id'] == 'question'
    second = store.event_page(run['id'], before=first['previous_event'])
    third = store.event_page(run['id'], before=second['previous_event'])
    assert len(third['events']) == 21 and not third['has_previous']
    combined = third['events'] + second['events'] + first['events']
    assert combined == store.events(run['id'])
    store.event(run['id'], 'operator_reply', {'message': 'answer', 'question_id': 'question'})
    delta = store.event_page(run['id'], after=first['next_event'])
    assert [event['kind'] for event in delta['events']] == ['operator_reply']
    assert store.pending_question(run['id']) is None
    assert len(store.events(run['id'])) == 422
    for options in ({'limit': 201}, {'after': True}, {'after': 1, 'before': 5}):
        with pytest.raises(ValueError):
            store.event_page(run['id'], **options)


def test_run_cursor_ties_owner_and_unfinished_are_not_lost_after_100(tmp_path):
    store = Store(tmp_path)
    first = make_run(store)
    with store.tx() as db:
        db.execute("UPDATE runs SET status='completed',reserved=0 WHERE id=?", (first['id'],))
        for index in range(105):
            db.execute('''INSERT INTO runs(id,request_key,owner,instruction,domains,status,month,cap,reserved,created,updated)
                VALUES(?,?,?,'history','["hospital"]',?,'2026-10',100,0,?,?)''',
                (f'run-{index:03}', f'key-{index}', 'operator', 'waiting_input' if index == 0 else 'completed', first['created'], first['created']))
    page = store.run_history('operator')
    older = store.run_history('operator', **page['next'])
    assert len(page['items']) == 100 and len(older['items']) == 6
    assert len({item['id'] for item in page['items'] + older['items']}) == 106
    assert [item['id'] for item in store.run_history('operator', unfinished=True)['items']] == ['run-000']
    assert store.run_history('other')['items'] == []
    assert store.find_request('operator', 'key-0')['id'] == 'run-000'
    assert store.find_request('other', 'key-0') is None


def test_usage_is_monotonic_and_repeated_receipts_do_not_grow_history(tmp_path):
    store = Store(tmp_path)
    run = make_run(store)
    usage = {'total_input_tokens': 10, 'total_output_tokens': 20, 'total_thought_tokens': 3}
    assert store.gemini_usage_totals(run['id'], 'turn-1', usage) == usage
    assert store.gemini_usage_totals(run['id'], 'turn-1', usage) == usage
    assert len(store.events(run['id'], kinds=('gemini_usage',))) == 1
    assert store.gemini_usage_totals(run['id'], 'turn-2', usage) == {key: value * 2 for key, value in usage.items()}
    assert store.gemini_usage_totals(run['id'], 'turn-1', {key: 0 for key in usage}) == {key: value * 2 for key, value in usage.items()}
    with pytest.raises(Conflict):
        store.gemini_usage_totals(run['id'], 'turn-3', {**usage, 'total_input_tokens': -1})


@pytest.mark.parametrize('suffix', ['', '/events'])
def test_browser_history_does_not_return_provider_checkpoints_or_native_payloads(tmp_path, monkeypatch, suffix):
    from fastapi.testclient import TestClient
    from experiments.agent_assistant.app import create_app
    monkeypatch.setenv('SAWA_TRIAL_OPERATOR_ACCOUNT', 'operator')
    app = create_app({}, tmp_path)
    store = app.state.store
    run = make_run(store)
    store.update(run['id'], status='completed', reserved=0)
    large = 'retained-native-evidence-' * 100000
    store.event(run['id'], 'gemini_state', {'interaction':'known', 'results':[large]})
    store.event(run['id'], 'tool_result', {'tool':'read_tool_result', 'result':{'ok':True, 'data':large}})
    store.event(run['id'], 'tool_result', {'tool':'ask_operator', 'result':{
        'waiting_input':True, 'question':'原文を保持しますか', 'options':['保持'], 'command_id':'question'}})
    store.event(run['id'], 'fax_decision', {'fax_id':'fax', 'approve':False, 'message':'原文を保持'})
    before = store.events(run['id'])
    with TestClient(app) as client:
        response = client.get('/runs/' + run['id'] + suffix)
        assert response.status_code == 200
        assert len(response.content) < 100000
        payload = response.json()
        assert [event['id'] for event in payload['events']] == [event['id'] for event in before]
        assert payload['events'][0]['payload'] == {}
        assert payload['events'][1]['payload']['result'] == {'ok':True}
        assert any(item.get('question_id') == 'question' for item in payload['conversation'])
        assert any('原文を保持' in item['text'] for item in payload['conversation'])
    assert store.events(run['id']) == before
