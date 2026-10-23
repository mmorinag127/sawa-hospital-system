"""Synthetic safety/contract checks; these are not real OCR or live Claude proofs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.catalog import OPERATIONS, build_path
from experiments.agent_assistant.managed import Managed
from experiments.agent_assistant.mcp_server import build_mcp, call_local
from experiments.agent_assistant.sandbox import Sandbox
from experiments.agent_assistant.store import Store, Conflict
from experiments.agent_assistant.tools import Tools


def prepared(tmp_path, domains=("hospital",)):
    configs = {}
    for name in domains:
        base = tmp_path / ("baseline-"+name)
        base.mkdir()
        (base / "trial-manifest.json").write_text(json.dumps({"purpose": "sawa-agent-isolated-trial"}))
        with sqlite3.connect(base / "database.sqlite3") as db:
            if name == 'school-lunch':
                db.executescript('CREATE TABLE school_lunch_menu_executions(id INTEGER,facility_id INTEGER,service_date TEXT,name TEXT); CREATE TABLE school_lunch_daily_work_schedules(facility_id INTEGER,service_date TEXT); CREATE TABLE school_lunch_route_diagrams(facility_id INTEGER,service_date TEXT);')
            db.execute("CREATE TABLE quantities(id TEXT PRIMARY KEY, value INTEGER)")
            db.execute("INSERT INTO quantities VALUES('original',7)")
        (base / "original.pdf").write_bytes(b"synthetic source bytes, not an OCR test")
        configs[name] = {"baseline": str(base)}
    store = Store(tmp_path / "runtime")
    store.set_budget(1000)
    run, _ = store.create("user", "request-key", "試験", list(domains), 100)
    sandbox = Sandbox(tmp_path / "runtime", configs)
    sandbox.prepare(run)
    store.update(run["id"], status="running")
    tools = Tools(store, sandbox)
    return store, sandbox, tools, store.get(run["id"])


def test_atomic_monthly_reservation_and_idempotency(tmp_path):
    store = Store(tmp_path)
    store.set_budget(100)
    def create(index):
        try:
            return store.create("user", f"key-{index:08}", "依頼", ["shift"], 60)[0]
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(create, [1, 2]))
    assert sum(row is not None for row in rows) == 1
    row = next(row for row in rows if row)
    repeat, fresh = store.create("user", row["request_key"], "依頼", ["shift"], 60)
    assert not fresh and repeat["id"] == row["id"]
    with pytest.raises(Conflict):
        store.create("user", row["request_key"], "different", ["shift"], 60)
    assert store.budget()["reserved_cents"] == 60


def test_usage_cumulative_cache_and_unknown_cost(tmp_path):
    store, _, _, run = prepared(tmp_path)
    usage = {"input_tokens": 30, "cache_read_input_tokens": 20, "list_cost": {"amount": "17", "currency": "USD"}}
    store.usage(run["id"], usage)
    store.usage(run["id"], usage)
    assert store.budget()["spent_cents"] == 17
    assert store.budget()["reserved_cents"] == 83
    with pytest.raises(Conflict):
        store.usage(run["id"], {"input_tokens": 50})
    assert store.budget()["reserved_cents"] == 83
    store.usage(run["id"], usage, terminal=True)
    assert store.budget()["reserved_cents"] == 0
    store.usage(run["id"], usage)  # Refresh after stop must not reserve the budget again.
    assert store.budget()["reserved_cents"] == 0


@pytest.mark.parametrize("domain", ["hospital", "shift", "school-lunch"])
def test_restore_is_per_request_and_preserves_original_and_audit(tmp_path, domain):
    store, sandbox, _, run = prepared(tmp_path, (domain,))
    copy = sandbox.path(run["id"]) / "data" / domain
    with sqlite3.connect(copy / "database.sqlite3") as db:
        db.execute("UPDATE quantities SET value=1")
    (copy / "output.xlsx").write_bytes(b"trial output")
    result = asyncio.run(sandbox.restore(run))
    with sqlite3.connect(copy / "database.sqlite3") as db:
        assert db.execute("SELECT value FROM quantities").fetchone()[0] == 7
    with sqlite3.connect(Path(sandbox.configs[domain]["baseline"]) / "database.sqlite3") as db:
        assert db.execute("SELECT value FROM quantities").fetchone()[0] == 7
    assert not (copy / "output.xlsx").exists()
    assert (sandbox.path(run["id"]) / result["archive"] / domain / "output.xlsx").is_file()
    assert store.get(run["id"])["status"] == "running"  # Restoration never rewrites history implicitly.


def test_baseline_symlink_and_missing_manifest_fail_closed(tmp_path):
    store, sandbox, _, run = prepared(tmp_path)
    base = Path(sandbox.configs["hospital"]["baseline"])
    (base / "escape").symlink_to(tmp_path)
    other = dict(run, id="a"*32)
    with pytest.raises(Conflict, match="シンボリック"):
        sandbox.prepare(other)


def test_fenced_writes_and_unknown_outcome_never_replay(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path)
    store.fence(run["id"])
    with pytest.raises(Conflict, match="停止"):
        asyncio.run(tools.execute(run["id"], 0, "hospital_list_orders", {"parameters": {}}, "late"))
    assert store.command(run["id"], "unknown", {"name": "write"}) is None
    with pytest.raises(Conflict, match="未確定"):
        store.command(run["id"], "unknown", {"name": "write"})


def test_native_mcp_bridge_uses_schema_and_exact_revision(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path)
    observed = []
    async def native(run, domain, method, path, **kwargs):
        observed.append((domain, method, path, kwargs))
        return httpx.Response(200, json={"workflow": {"revision": 6}})
    sandbox.request = native
    mcp = build_mcp(tools)
    args = {"parameters": {"ids": {"order_id": "ORD-1"}, "expected_revision": 5,
                           "body": {"sheet": {}}, "reason": "利用者が原本と照合"}}
    result = asyncio.run(call_local(mcp, tools, run["id"], 0, "hospital_save_sheet", args, "event-1"))
    assert result["ok"]
    assert observed[0][3]["revision"] == 5
    assert observed[0][2] == "/orders/ORD-1/workflow-v2/sheet"
    asyncio.run(call_local(mcp, tools, run["id"], 0, "hospital_save_sheet", args, "event-1"))
    assert len(observed) == 1


def test_missing_revision_and_scope_and_traversal(tmp_path):
    store, _, tools, run = prepared(tmp_path)
    result = asyncio.run(tools.execute(run["id"], 0, "hospital_save_sheet", {"parameters": {"ids": {"order_id": "a"}, "reason": "test"}}, "missing"))
    assert "revision" in result["error"]
    other = asyncio.run(tools.execute(run["id"], 0, "shift_get_staff", {"parameters": {}}, "scope"))
    assert "許可" in other["error"]
    with pytest.raises(ValueError):
        build_path(OPERATIONS["hospital_get_order"], {"order_id": "../../worker"})


def test_model_cannot_claim_completion_from_mutation_or_failed_call(tmp_path):
    store, _, tools, run = prepared(tmp_path)
    store.command(run["id"], "mutation", {"name": "hospital_save_sheet", "arguments": {}})
    store.finish_command(run["id"], "mutation", {"ok": True})
    with pytest.raises(Conflict):
        tools.finish(run["id"], {"summary": "done", "evidence_commands": ["mutation"], "artifact_ids": []})
    with pytest.raises(Conflict):
        tools.finish(run["id"], {"summary": "done", "evidence_commands": ["nonexistent"], "artifact_ids": []})


def test_app_auth_csrf_budget_missing_provider_and_no_auto_spend(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_AGENT_ID", "CLAUDE_ENVIRONMENT_ID"):
        monkeypatch.delenv(name, raising=False)
    app = create_app({}, tmp_path)
    with TestClient(app) as client:
        assert client.get("/config").status_code == 401
        assert client.get("/").status_code == 200
        assert len(client.get("/config").json()["missing"]) == 3
        assert client.put("/budget", json={"monthly_cents": 100}, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.put("/budget", json={"monthly_cents": 100}).status_code == 200
        assert client.put("/budget", json={"monthly_cents": 1.5}).status_code == 422
        r = client.post("/runs", json={"request_key": "test-request", "instruction": "試用", "domains": ["hospital"], "cap_cents": 10})
        assert r.status_code == 409
        assert client.get("/budget").json()["spent_cents"] == 0
        assert client.get("/runs").json() == []


class Event:
    def __init__(self, **values): self.values = values
    def model_dump(self, **_): return self.values


class FakeStream:
    def __init__(self, events): self.events = iter(events)
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def close(self): pass
    def __aiter__(self): return self
    async def __anext__(self):
        try: return next(self.events)
        except StopIteration: raise StopAsyncIteration


def test_managed_custom_tools_cost_and_idle_not_false_completion(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path, ("shift",))
    store.update(run["id"], session_id="sesn-test")
    sent = []
    async def native(*args, **kwargs): return httpx.Response(200, json=[])
    sandbox.request = native
    class Events:
        async def stream(self, _):
            return FakeStream([Event(type="agent.custom_tool_use", id="ev1", name="shift_get_staff", input={"parameters": {}}),
                               Event(type="session.usage", id="ev2", usage={"list_cost": {"amount": "12", "currency": "USD"}}),
                               Event(type="session.status_idle", id="ev3", stop_reason={"type": "end_turn"})])
        async def send(self, _, **kwargs): sent.append(kwargs)
    class Sessions:
        events = Events()
        async def retrieve(self, _): return SimpleNamespace(status="idle", usage=Event(list_cost={"amount": "12", "currency": "USD"}))
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=Sessions())))
    asyncio.run(managed.consume(run["id"]))
    assert sent[0]["events"][0]["custom_tool_use_id"] == "ev1"
    assert store.get(run["id"])["status"] == "waiting_input"
    assert store.get(run["id"])["spent"] == 12


def test_recovery_keeps_unsettled_reservation(tmp_path):
    store, _, _, run = prepared(tmp_path)
    store.recover()
    assert store.get(run["id"])["status"] == "reconciliation_required"
    assert store.budget()["reserved_cents"] == 100


def test_mcp_http_handshake_and_operator_question(tmp_path):
    app = create_app({}, tmp_path)
    # This case tests MCP HTTP dispatch; SAWA permission denial is checked separately.
    app.state.tools.authorization_validator = None
    store = app.state.store
    store.set_budget(100)
    run, _ = store.create("local-trial-operator", "mcp-http-request", "質問を確認", ["shift"], 10)
    store.update(run["id"], status="running")
    with TestClient(app, base_url="http://127.0.0.1:8128") as client:
        client.get("/")
        headers = {"Accept": "application/json, text/event-stream", "X-Run-ID": run["id"], "Idempotency-Key": "http-tool-1"}
        result = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "contract-test", "version": "1"}}})
        assert result.status_code == 200 and "result" in result.json()
        result = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in result.json()["result"]["tools"]}
        assert names >= {"hospital_save_sheet", "shift_solve", "lunch_get_menus", "ask_operator", "finish_work", "describe_operation", "get_trial_jobs"}
        result = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ask_operator", "arguments": {"question": "対象月を指定してください"}}})
        assert result.status_code == 200
        assert store.get(run["id"])["status"] == "waiting_input"


@pytest.mark.parametrize("data", [{"status": "queued"}, {"workflow": {"blockers": ["missing-source"]}}, {"job_status": {"status": "running"}}, {"workflow": "unresolved", "stale": True}])
def test_pending_native_results_cannot_be_completion_evidence(tmp_path, data):
    store, _, tools, run = prepared(tmp_path)
    store.command(run["id"], "read", {"name": "hospital_get_workflow", "arguments": {"parameters": {"ids": {"order_id": "test"}}}})
    store.finish_command(run["id"], "read", {"ok": True, "data": data})
    with pytest.raises(Conflict):
        tools.finish(run["id"], {"summary": "done", "evidence_commands": ["read"], "artifact_ids": []})
    assert store.get(run["id"])["status"] != "completed"


def test_schema_description_resolves_only_referenced_components(tmp_path):
    _, sandbox, tools, run = prepared(tmp_path)
    async def schema(*args, **kwargs):
        return httpx.Response(200, json={"paths": {"/orders/{id}/workflow-v2/sheet": {"put": {"requestBody": {"$ref": "#/components/schemas/Sheet"}}}},
            "components": {"schemas": {"Sheet": {"type": "object", "properties": {"cell": {"$ref": "#/components/schemas/Cell"}}}, "Cell": {"type": "integer"}, "Unrelated": {"type": "string"}}}})
    sandbox.request = schema
    result = asyncio.run(tools.describe(run, {"operation": "hospital_save_sheet"}))
    assert set(result["components"]["schemas"]) == {"Sheet", "Cell"}
    assert result['wrapper_parameters']['ids'] == ['order_id']
    with pytest.raises(ValueError):
        asyncio.run(tools.describe(run, {"operation": "shift_solve"}))


def test_managed_stream_opens_before_message_and_waits_for_human(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path, ("shift",))
    store.update(run["id"], session_id="sesn-test")
    order = []
    class Events:
        async def stream(self, _):
            order.append("open")
            return FakeStream([Event(type="agent.custom_tool_use", id="question-1", name="ask_operator", input={"question": "対象月は？"})])
        async def send(self, _, **kwargs): order.append(kwargs["events"][0]["type"])
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace(events=Events()))))
    async def scenario():
        await managed.follow(run["id"], {"type": "user.message", "content": [{"type": "text", "text": "test"}]})
        await managed.tasks[run["id"]]
    asyncio.run(scenario())
    assert order == ["open", "user.message"]  # Pending question does not resume the provider.
    assert store.get(run["id"])["status"] == "waiting_input"


def test_stop_reaps_local_writes_even_when_provider_stop_unknown(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path)
    store.update(run["id"], session_id="sesn-unknown")
    calls = []
    async def local_stop(ident): calls.append(ident)
    sandbox.stop = local_stop
    class Sessions:
        async def retrieve(self, _): raise httpx.ConnectError("synthetic network failure")
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=Sessions())))
    asyncio.run(managed.stop(run["id"]))
    assert calls == [run["id"]]
    assert store.get(run["id"])["status"] == "stop_pending"
    assert store.get(run["id"])["generation"] == 1
    assert store.get(run["id"])["reserved"] == run["cap"]


def test_retrying_remote_stop_preserves_completed_local_rollback(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path)
    store.update(run["id"], status="rollback_stop_pending", session_id="sesn-stopped")
    class Sessions:
        async def retrieve(self, _): return SimpleNamespace(status="idle", usage=Event(list_cost={"amount": "7", "currency": "USD"}))
        async def archive(self, _): pass
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=Sessions())))
    asyncio.run(managed.stop(run["id"]))
    assert store.get(run["id"])["status"] == "rolled_back"
    assert store.get(run["id"])["spent"] == 7
    assert store.get(run["id"])["reserved"] == 0


def test_stop_during_api_boot_cancels_start_before_remote_spend(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path)
    store.update(run["id"], status="preparing")
    started = asyncio.Event()
    stopped = []
    async def boot(_):
        started.set()
        await asyncio.Event().wait()
    async def stop(ident, *, expected_generation=None):
        stopped.append(ident)
        assert expected_generation == run['generation'] + 1
        return True
    sandbox.start, sandbox.stop = boot, stop
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=object())))
    async def scenario():
        task = asyncio.create_task(managed.start(run))
        await started.wait()
        await managed.stop(run["id"])
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
    asyncio.run(scenario())
    assert stopped == [run["id"]]
    assert store.get(run["id"])["status"] == "stopped"
    assert store.get(run["id"])["reserved"] == 0


def test_operator_reply_answers_exact_pending_custom_call(tmp_path):
    store, sandbox, tools, run = prepared(tmp_path, ("shift",))
    store.update(run["id"], session_id="sesn-question")
    sent = []
    class Events:
        count = 0
        async def stream(self, _):
            self.count += 1
            if self.count == 1:
                return FakeStream([Event(type="agent.custom_tool_use", id="question-identity", name="ask_operator", input={"question": "対象月は？"})])
            return FakeStream([Event(type="session.status_idle", id="idle-after-reply", stop_reason={"type": "end_turn"})])
        async def send(self, _, **kwargs): sent.extend(kwargs["events"])
    class Sessions:
        events = Events()
        async def retrieve(self, _):
            return SimpleNamespace(status="idle", usage=Event(list_cost={"amount": "2", "currency": "USD"}))
    async def boot(_): pass
    sandbox.start = boot
    managed = Managed(store, sandbox, tools, build_mcp(tools), {}, client=SimpleNamespace(beta=SimpleNamespace(sessions=Sessions())))
    async def scenario():
        await managed.consume(run["id"])
        await managed.reply(run["id"], "2026年10月です")
        await managed.tasks[run["id"]]
    asyncio.run(scenario())
    assert len(sent) == 1 and sent[0]["type"] == "user.custom_tool_result"
    assert sent[0]["custom_tool_use_id"] == "question-identity"
    assert "2026年10月" in sent[0]["content"][0]["text"]


def test_completion_requires_related_saved_entity(tmp_path):
    _, _, tools, _ = prepared(tmp_path, ("shift",))
    write = OPERATIONS["shift_update_staff"]
    read = OPERATIONS["shift_get_staff"]
    assert tools.covers(write, {"staff_id": 7}, read, {}, {"items": [{"id": 7, "name": "Synthetic"}]})
    assert not tools.covers(write, {"staff_id": 7}, read, {}, {"items": [{"id": 8}]})
    assert not tools.covers(write, {}, OPERATIONS["shift_get_runs"], {}, [])


def test_http_rollback_preserves_charges_history_and_rejects_old_artifacts(tmp_path):
    _, baseline, _, _ = prepared(tmp_path)
    class Sessions:
        async def retrieve(self, _): return SimpleNamespace(status="idle", usage=Event(list_cost={"amount": "17", "currency": "USD"}))
        async def archive(self, _): pass
    class Client:
        beta = SimpleNamespace(sessions=Sessions())
        async def close(self): pass
    app = create_app({"domains": baseline.configs}, tmp_path / "app", managed_client=Client())
    store = app.state.store
    store.set_budget(100)
    run, _ = store.create("local-trial-operator", "rollback-http", "合成データの復元試験", ["hospital"], 50)
    app.state.sandbox.prepare(run)
    store.update(run["id"], status="running", session_id="sesn-synthetic")
    artifact = app.state.tools.artifact(run["id"], b"synthetic-result", "component.txt")
    with TestClient(app) as client:
        client.get("/")
        result = client.post(f"/runs/{run['id']}/rollback")
        assert result.status_code == 200 and result.json()["status"] == "rolled_back"
        assert client.get("/budget").json()["spent_cents"] == 17
        assert client.get("/budget").json()["reserved_cents"] == 0
        assert client.get(f"/runs/{run['id']}/artifacts/{artifact['id']}").status_code == 409
        assert client.post(f"/runs/{run['id']}/rollback").json()["status"] == "rolled_back"
        assert sum(event["kind"] == "rollback" for event in store.events(run["id"])) == 1


@pytest.mark.parametrize("case", ["allowed", "wrong_account", "missing_system", "expired", "stop_during_auth"])
def test_each_background_tool_rechecks_sawa_authorization(tmp_path, monkeypatch, case):
    monkeypatch.setenv("SAWA_TRIAL_OPERATOR_ACCOUNT", "trial@example.invalid")
    app = create_app({"portal_auth_url": "https://auth.invalid/portal/auth/me"}, tmp_path)
    store = app.state.store
    store.set_budget(100)
    run, _ = store.create("trial@example.invalid", "auth-boundary", "権限境界の合成試験", ["shift"], 10)
    store.update(run["id"], status="running")
    tools = app.state.tools
    tools.authorizations[run["id"]] = "Bearer synthetic-not-a-credential"
    async def auth(request):
        assert str(request.url) == "https://auth.invalid/portal/auth/me"
        if case == "stop_during_auth":
            store.fence(run["id"])
        return httpx.Response(401 if case in {"expired", "stop_during_auth"} else 200,
            json={"account": "other@example.invalid" if case == "wrong_account" else "trial@example.invalid",
                  "systems": [] if case == "missing_system" else ["shift"]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(auth), **kwargs))
    invoked = []
    async def native(*args, **kwargs):
        invoked.append(True)
        return httpx.Response(200, json=[])
    app.state.sandbox.request = native
    result = asyncio.run(tools.execute(run["id"], 0, "shift_get_staff", {"parameters": {}}, "auth-call"))
    assert bool(invoked) == (case == "allowed")
    if case == "allowed":
        assert result["ok"]
    elif case == "stop_during_auth":
        assert store.get(run["id"])["status"] == "stopping"
    else:
        assert result["needs_operator_confirmation"] and store.get(run["id"])["status"] == "waiting_input"


@pytest.mark.parametrize('status,receipt,allowed', [
    ('rollback_stop_pending', {'restored':True,'scope':'this_request_sandbox'}, True),
    ('rollback_stop_pending', {}, False),
    ('rollback_stop_pending', {'restored':False,'scope':'this_request_sandbox'}, False),
    ('rollback_stop_pending', {'restored':True,'scope':'other'}, False),
    ('stop_pending', {'restored':True,'scope':'this_request_sandbox'}, False),
    ('reconciliation_required', {'restored':True,'scope':'this_request_sandbox'}, False),
])
def test_only_verified_restoration_releases_active_sandbox_without_refunding(tmp_path,status,receipt,allowed):
    store=Store(tmp_path);store.set_budget(500)
    old,_=store.create('u','old-key','old',['hospital'],200,provider='gemini')
    store.update(old['id'],status=status,spent=17,reserved=183)
    store.event(old['id'],'rollback',receipt)
    if allowed:
        new,_=store.create('u','new-key','new',['school-lunch'],300,provider='gemini')
        assert new['status']=='preparing'
        assert store.budget()['available_cents']==0
        with pytest.raises(Conflict):store.create('u','over-key','over',['shift'],1)
    else:
        with pytest.raises(Conflict,match='他の依頼'):store.create('u','new-key','new',['shift'],100)
    assert store.get(old['id'])['spent']==17 and store.get(old['id'])['reserved']==183
