"""Local trial API. Start with python -m experiments.agent_assistant.app."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse

import httpx

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .providers import Providers, LABELS
from .mcp_server import build_mcp
from .sandbox import Sandbox
from .store import Store, Conflict
from .tools import Tools
from .tasks import TASKS, public_tasks
from .conversation import conversation, pending_question


def trial_cookie_name(request):
    # Cookies are scoped by host, not TCP port. Keep simultaneously opened
    # localhost previews from replacing one another's authentication token.
    return 'sawa_trial_'+str(request.url.port or 80)


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=8, max_length=128)
    task_id: str | None = None
    selection_id: str | None = None
    target_keys: list[str] = Field(default_factory=list)
    layout_id: int | None = None
    instruction: str = Field(default="", max_length=20000)
    domains: list[str] = Field(default_factory=list)
    cap_cents: StrictInt | None = Field(default=None, ge=1, le=100000)


class TargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict = Field(default_factory=dict)


class TargetLookupRequest(TargetRequest):
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')


class FaxDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    approve: bool
    message: str = Field(default="", max_length=4000)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_cents: StrictInt = Field(ge=0, le=10000000)


class ProviderChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=20000)
    question_id: str | None = None
    option: str | None = None


def create_app(config, root, *, managed_client=None, gemini_client=None):
    if config.get("durability") == "cloud-sql":
        from .durable import cloud_store
        store = cloud_store(Path(root))
    else:
        store = Store(Path(root))
    if getattr(store, "new_ledger", False) and config.get("initial_provider"):
        store.provider(config["initial_provider"])
    store.recover()
    sandbox = Sandbox(root, config.get("domains", {}), durable_store=store if config.get("durability") == "cloud-sql" else None)
    if config.get("portal_auth_url") and "hospital" in sandbox.configs:
        hospital = sandbox.configs["hospital"]
        hospital["env"] = {**hospital.get("env", {}), "AUTH_PROVIDER": "portal", "PORTAL_AUTH_ME_URL": config["portal_auth_url"]}
    tools = Tools(store, sandbox)
    mcp = build_mcp(tools)
    managed = Providers(store, sandbox, tools, mcp, config, client=managed_client, gemini_client=gemini_client)
    from .business_workflow import BusinessWorkflow
    from .business_contracts import CONTRACTS, owned
    business = BusinessWorkflow(store, tools, managed)
    from .target_lookups import TargetLookups
    lookups = TargetLookups(store, tools)
    secret = os.environ.get("SAWA_TRIAL_WEB_TOKEN") or secrets.token_urlsafe(32)
    owner = os.environ.get("SAWA_TRIAL_OPERATOR_ACCOUNT") or "local-trial-operator"
    controls = {}

    def control_lock(ident):
        return controls.setdefault(ident, asyncio.Lock())

    async def authorize(authorization, domains):
        if managed_client is not None or gemini_client is not None:
            return  # Dependency injection is available only to in-process tests.
        url = config.get("portal_auth_url", "")
        parsed = urlparse(url)
        if (not os.environ.get("SAWA_TRIAL_OPERATOR_ACCOUNT") or not authorization.startswith("Bearer ")
                or not parsed.hostname or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}))):
            raise Conflict("SAWAログイン・試用担当者・認証APIの管理者設定が必要です")
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(url, headers={"Authorization": authorization})
            identity = response.json() if response.is_success else {}
        except (httpx.HTTPError, ValueError):
            raise Conflict("SAWAの利用権限を確認できません。再ログイン後に回答してください") from None
        if identity.get("account") != owner or not set(domains).issubset(set(identity.get("systems") or [])):
            raise Conflict("対象業務を操作できるSAWAアカウントではありません")

    async def authorize_run(run):
        await authorize(tools.authorizations.get(run["id"], ""), run["domains"])
    tools.authorization_validator = authorize_run

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await business.close()
        await lookups.close()
        for task in managed.tasks.values():
            task.cancel()
        await asyncio.gather(*managed.tasks.values(), return_exceptions=True)
        for ident, _ in list(sandbox.children):
            await sandbox.stop(ident)
        if managed.client is not None:
            await managed.client.close()
        if managed.gemini.client is not None:
            await managed.gemini.client.aclose()

    app = FastAPI(title="SAWA 業務アシスタント試用", lifespan=lifespan)
    app.state.store, app.state.sandbox, app.state.tools = store, sandbox, tools
    app.state.managed, app.state.token = managed, secret
    app.state.business = business

    @app.middleware("http")
    async def local_auth(request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Local trial only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/") and not request.headers.get("x-trial-token"):
            return JSONResponse({"detail": "Cross-origin trial access denied"}, status_code=403)
        if request.url.path in ("/", "/admin") and config.get("durability") != "cloud-sql":
            response = FileResponse(Path(__file__).with_name("preview.html"))
            response.set_cookie(trial_cookie_name(request), secret, httponly=True, samesite="strict")
            return response
        if not hmac.compare_digest(request.headers.get("x-trial-token", request.cookies.get(trial_cookie_name(request), "")), secret):
            return JSONResponse({"detail": "Trial authentication required"}, status_code=401)
        if request.url.path.startswith("/mcp"):
            ident = request.headers.get("x-run-id", "")
            try:
                run = store.get(ident, owner)
            except KeyError:
                return JSONResponse({"detail": "Run required"}, status_code=403)
            request.state.trial_binding = (ident, run["generation"])
        response = await call_next(request)
        if sandbox.durable_store is not None and request.method not in ("GET", "HEAD", "OPTIONS") and '/target-lookups' not in request.url.path:
            for run in store.listing(owner):
                store.persist_files(run["id"])
        return response

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def bad_input(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(_request, exc):
        return JSONResponse({"detail": "対象が見つかりません"}, status_code=404)

    @app.get("/config")
    def configuration():
        domains = {}
        for name in ("hospital", "shift", "school-lunch"):
            cfg = config.get("domains", {}).get(name, {})
            baseline = Path(cfg.get("baseline", "/__not_configured__"))
            domains[name] = {"configured": bool(cfg), "baseline_ready": (baseline / "trial-manifest.json").is_file() and (baseline / "database.sqlite3").is_file(),
                             "data": "registered_staging_snapshot" if cfg.get('registered_source') else json.loads((baseline / "trial-manifest.json").read_text()).get("data") if (baseline / "trial-manifest.json").is_file() else None,
                             "ocr_pipeline_configured": bool(cfg.get("env", {}).get("OCR_PIPELINE_BUCKET")) if name == "hospital" else None}
        return {"trial": True, "provider_id": store.provider(), "provider": LABELS[store.provider()], "providers": managed.choices(), "missing": managed.missing(), "domains": domains,
                "budget": store.budget(), "notice": "専用コピーを使用。既存環境への反映はありません。"}

    @app.put('/provider')
    def select_provider(payload: ProviderChoice):
        store.provider(payload.provider)
        return configuration()

    @app.get("/tasks")
    def tasks():
        return public_tasks()

    @app.post("/tasks/{task_id}/targets")
    async def task_targets(task_id: str, payload: TargetRequest, request: Request):
        if task_id not in TASKS:
            raise ValueError("対象選択を行う作業項目を指定してください")
        await authorize(request.headers.get("authorization", ""), [TASKS[task_id]["domain"]])
        return await tools.flow.options(task_id, payload.inputs, owner, request.headers.get("authorization", ""))

    @app.post('/tasks/{task_id}/target-lookups', status_code=202)
    async def start_target_lookup(task_id: str, payload: TargetLookupRequest, request: Request):
        if task_id not in CONTRACTS:
            raise ValueError('対象選択を行う作業を選択してください')
        authorization = request.headers.get('authorization', '')
        await authorize(authorization, [TASKS[task_id]['domain']])
        return lookups.create(payload.request_key, owner, task_id, payload.inputs, authorization)

    @app.get('/target-lookups/{ident}')
    async def target_lookup(ident: str, request: Request):
        authorization = request.headers.get('authorization', '')
        # The lookup row is owner-bound. Revalidate its exact domain on every poll.
        with store.read() as db:
            row = db.execute('SELECT task_id FROM target_lookups WHERE id=? AND owner=?', (ident, owner)).fetchone()
        if not row:
            raise KeyError(ident)
        await authorize(authorization, [TASKS[row['task_id']]['domain']])
        return lookups.get(ident, owner, authorization)

    @app.get("/budget")
    def budget():
        return store.budget()

    @app.put("/budget")
    def set_budget(payload: Budget):
        # Local operator only; this trial is deliberately not a shared production service.
        month = store.budget()["month"]
        with store.tx() as db:
            allocated = db.execute("SELECT COALESCE(SUM(spent+reserved),0) FROM runs WHERE month=?", (month,)).fetchone()[0]
            if allocated > payload.monthly_cents:
                raise Conflict("使用済み・予約中の合計より小さく設定できません。実行中の依頼を停止してください")
            db.execute("UPDATE settings SET monthly_cents=? WHERE id=1", (payload.monthly_cents,))
        return store.budget()

    @app.get("/runs")
    async def runs(request: Request):
        listing = store.listing(owner)
        for run in listing:
            if owned(run):
                business.kick(run['id'], request.headers.get('authorization', ''))
        return [{**run, 'business': business.projection(run, tools.flow.review(run['id']), tools.faxes.items(run['id']) if tools.faxes.active(run) else [])} for run in listing]

    @app.post("/runs", status_code=201)
    async def create_run(payload: NewRun, request: Request):
        injected = gemini_client if store.provider() == 'gemini' else managed_client
        task = TASKS.get(payload.task_id) if payload.task_id else None
        if payload.task_id and payload.task_id not in CONTRACTS:
            raise ValueError('画面に表示されている業務を選択してください')
        if managed.missing() and injected is None and (not task or CONTRACTS[payload.task_id].prepare == 'agent'):
            raise Conflict("この業務の接続設定は管理者が確認しています。選択した内容は変更していません")
        if payload.task_id and (not task or payload.instruction or payload.domains):
            raise ValueError("定型業務は作業項目だけを指定してください")
        domains = [task["domain"]] if task else payload.domains
        instruction = task["label"] if task else payload.instruction.strip()
        if not instruction:
            raise ValueError("作業項目が必要です")
        await authorize(request.headers.get("authorization", ""), domains)
        task_inputs = {}
        if task and payload.task_id != "hospital_pending_faxes":
            task_inputs = tools.flow.selection(payload.task_id, owner, payload.selection_id, payload.target_keys, payload.layout_id)
        elif payload.selection_id or payload.target_keys or payload.layout_id is not None:
            raise ValueError("この作業では対象一覧の指定は使いません")
        run, fresh = store.create(owner, payload.request_key, instruction, domains, payload.cap_cents if payload.cap_cents is not None else config.get("run_cap_cents", 100), task_id=payload.task_id, task_inputs=task_inputs, provider=store.provider())
        if fresh:
            tools.authorizations[run["id"]] = request.headers.get("authorization", "")
            if task:
                business.initialize(run)
            try:
                sandbox.prepare(run)
            except Exception as exc:
                store.update(run["id"], expected_generation=run['generation'], status="failed", reserved=0)
                store.event(run["id"], "error", {"message": str(exc) if isinstance(exc, Conflict) else "試用データの準備に失敗しました"})
                return store.get(run["id"], owner)
            if payload.task_id == "lunch_import":
                store.update(run["id"], expected_generation=run['generation'], status="waiting_input")
                store.event(run["id"], "notice", {"message":"選択した学校・対象月と一致する原本を添付してください。添付が終わるまでエージェントは開始しません。"})
                return store.get(run["id"],owner)
            if task:
                business.kick(run['id'])
                return store.get(run['id'], owner)
            # Remote create/send must not be retried after an unknown outcome.
            try:
                await managed.start(run)
            except asyncio.CancelledError:
                if store.get(run["id"])["status"] in ("stopping", "stopped", "stop_pending", "rolled_back", "rollback_stop_pending"):
                    return store.get(run["id"], owner)
                raise
            except Exception as exc:
                await sandbox.stop(run["id"])
                current = store.get(run['id'])
                if current['generation'] != run['generation'] or current['status'] in ('completed', 'stopping', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending'):
                    return current
                if current["status"] == "preparing":
                    store.update(run["id"], expected_generation=run['generation'], status="failed", reserved=0)
                else:
                    store.update(run["id"], expected_generation=run['generation'], status="reconciliation_required")
                store.event(run["id"], "error", {"message": LABELS[run["provider"]]+"開始の結果が未確定です。自動再送と予算返却を停止しています。", "class": type(exc).__name__})
        return store.get(run["id"], owner)

    @app.get("/runs/{ident}")
    async def run_detail(ident: str, request: Request):
        run = store.get(ident, owner)
        if not owned(run) and run.get('task_id') in CONTRACTS and run['status'] in ('running', 'waiting_input', 'reconciliation_required'):
            await authorize(request.headers.get('authorization', ''), run['domains'])
            business.adopt(run)
            run = store.get(ident, owner)
        if owned(run):
            business.kick(ident, request.headers.get('authorization', ''))
        with store.snapshot():
            run = store.get(ident, owner)
            with store.read() as db:
                artifacts = [dict(row) for row in db.execute("SELECT id,name,sha256,size FROM artifacts WHERE run_id=?", (ident,))]
            events = store.events(ident)
            from .shift_review import build_shift_review
            from .lunch_review import build_lunch_review
            review = tools.flow.review(ident) if run['status'] not in ('rolled_back','rollback_stop_pending') else None
            reference = next((event['payload'] for event in events if event['kind']=='shift_reference'),None)
            shift_review = build_shift_review(review,reference) if review and (run.get('task_id') or '').startswith('shift_') else None
            lunch_review = build_lunch_review(review,run.get('task_inputs',{}).get('process_questions')) if run.get('task_id')=='lunch_daily_bundle' else None
            faxes = tools.faxes.items(ident) if tools.faxes.active(run) and run["status"] not in ("rolled_back", "rollback_stop_pending") else []
            business_events = [event for event in events if event['kind'] in ('operator_reply', 'task_decision', 'fax_decision', 'task_review', 'fax_review') or event['kind'] == 'tool_result' and event['payload'].get('result', {}).get('waiting_input')]
            fax_labels = {str(item['fax_id']): item['snapshot'].get('name') or '対象のFAX' for item in faxes}
            return {**run, 'business': business.projection(run, review, faxes), 'business_messages': conversation(business_events, fax_labels) if owned(run) else [], "lunch_review": lunch_review, "shift_review": shift_review, "conversation": conversation(events), "pending_question": pending_question(events) if run["status"] == "waiting_input" else None, "events": events, "artifacts": artifacts, "local_jobs": sandbox.local_jobs(run), "task_review": review, "fax_items": faxes}

    @app.post("/runs/{ident}/faxes/{fax_id}/decision")
    async def decide_fax(ident: str, fax_id: str, payload: FaxDecision, request: Request):
        run = store.get(ident, owner)
        await authorize(request.headers.get("authorization", ""), run["domains"])
        tools.authorizations[ident] = request.headers.get("authorization", "")
        async with tools.lock(ident):
            item = next((item for item in tools.faxes.items(ident) if item['fax_id'] == fax_id), None)
            decision = {'review_id': payload.review_id, 'approve': payload.approve, 'message': payload.message}
            if owned(run) and item and item.get('decision') and all(item['decision'].get(key) == value for key, value in decision.items()):
                result = {'ok': True, 'replayed': True}
            else:
                result = await tools.faxes.decide(store.get(ident, owner), fax_id, payload.review_id, payload.approve, payload.message)
        if owned(run):
            business.decision_ready(ident, payload.approve)
        return result

    @app.post("/runs/{ident}/decision")
    async def task_decision(ident: str, payload: FaxDecision, request: Request):
        run=store.get(ident,owner)
        await authorize(request.headers.get("authorization", ""),run["domains"])
        tools.authorizations[ident]=request.headers.get("authorization", "")
        async with tools.lock(ident):
            review = tools.flow.review(ident)
            if review and review['id'] == payload.review_id and review.get('decision') == {'approve': payload.approve, 'message': payload.message}:
                result = {'ok': True, 'replayed': True}
            else:
                result = await tools.flow.decide(store.get(ident,owner),payload.review_id,payload.approve,payload.message)
        if owned(run):
            business.decision_ready(ident, payload.approve)
        return result

    @app.post("/runs/{ident}/reply")
    async def reply(ident: str, payload: Reply, request: Request):
        run=store.get(ident, owner)
        tools.authorizations[ident] = request.headers.get("authorization", "")
        await authorize(request.headers.get('authorization', ''), run['domains'])
        if owned(run):
            replies = [event['payload'] for event in store.events(ident) if event['kind'] == 'operator_reply']
            if payload.question_id and any(item.get('question_id') == payload.question_id and item.get('message') == payload.message and item.get('option') == payload.option for item in replies):
                return store.get(ident, owner)
            if business.shift_questions.answer(run, payload.message, payload.question_id, payload.option) or business.answer_layout(run, payload.message, payload.question_id, payload.option):
                return store.get(ident, owner)
            if run['task_id'] == 'lunch_import' and business.state(ident)['phase'] == 'source':
                if not any(event['kind'] == 'source' for event in store.events(ident)):
                    raise Conflict('選択した学校・月と一致する原本を添付してください')
                business.source_ready(run)
                return store.get(ident, owner)
            await managed.reply(ident, payload.message, payload.question_id, payload.option)
            business.checkpoint(ident, phase='analysing')
            business.kick(ident)
            return store.get(ident, owner)
        if run.get("task_id")=="lunch_import" and not run["session_id"]:
            await authorize(request.headers.get("authorization", ""),run["domains"])
            if run["status"] != "waiting_input" or not any(e["kind"]=="source" for e in store.events(ident)):
                raise Conflict("原本を添付してから開始してください")
            async with control_lock(ident):
                if store.get(ident)["status"] != "waiting_input" or store.get(ident)["session_id"]:
                    raise Conflict("既に開始済み、または停止されています")
                store.update(ident,expected_generation=run['generation'],status="preparing")
            try:
                await managed.start(store.get(ident))
            except asyncio.CancelledError:
                if store.get(ident)["status"] not in ("stopping","stopped","rolled_back","stop_pending","rollback_stop_pending"): raise
            except Exception:
                await sandbox.stop(ident)
                store.update(ident,expected_generation=run['generation'],status="reconciliation_required")
                store.event(ident,"error",{"message":"開始結果を照合してください。自動再送は行いません。"})
                raise
            return store.get(ident,owner)
        await managed.reply(ident, payload.message, payload.question_id, payload.option)
        return store.get(ident, owner)

    @app.post("/runs/{ident}/resume")
    async def resume(ident: str, request: Request):
        run = store.get(ident, owner)
        await authorize(request.headers.get('authorization', ''), run['domains'])
        async with control_lock(ident):
            tools.authorizations[ident] = request.headers.get('authorization', '')
            return await managed.resume(ident)

    @app.post('/runs/{ident}/reconcile-business')
    async def reconcile_business(ident: str, request: Request):
        run = store.get(ident, owner)
        await authorize(request.headers.get('authorization', ''), run['domains'])
        if not owned(run):
            raise Conflict('共通業務進行を利用している依頼ではありません')
        business.checkpoint(ident, attempts=0, next_attempt=0)
        business.kick(ident, request.headers.get('authorization', ''))
        return store.get(ident, owner)

    @app.post("/runs/{ident}/stop")
    async def stop(ident: str):
        store.get(ident, owner)
        async with control_lock(ident):
            await managed.stop(ident)
        return store.get(ident, owner)

    @app.post("/runs/{ident}/rollback")
    async def rollback(ident: str):
        async with control_lock(ident):
            return await restore_run(ident)

    async def restore_run(ident):
        run = store.get(ident, owner)
        if run["status"] == "rolled_back":
            return run
        if run["status"] == "rollback_stop_pending":
            await managed.stop(ident)
            return store.get(ident, owner)
        await managed.stop(ident)
        async with tools.lock(ident):
            result = await sandbox.restore(store.get(ident, owner))
            store.event(ident, "rollback", result)
            # Keep remote-stop uncertainty visible even though local restoration succeeded.
            remote_pending = store.get(ident)["status"] == "stop_pending"
            store.update(ident, status="rollback_stop_pending" if remote_pending else "rolled_back")
        return store.get(ident, owner)

    @app.post("/runs/{ident}/refresh-usage")
    async def refresh(ident: str):
        run = store.get(ident, owner)
        if run.get("task_id")=="lunch_import" and not run["session_id"] and run["status"] in ("waiting_input","stopped","rolled_back"):
            return {"run":run,"budget":store.budget(),"notice":"モデル開始前のためモデル利用料金はありません。"}
        await managed.refresh_usage(ident)
        return {"run": store.get(ident), "budget": store.budget()}

    @app.post("/runs/{ident}/sources")
    async def source(ident: str, request: Request, file: UploadFile = File(), domain: str = Form(), binding: str = Form()):
        run = store.get(ident, owner)
        if run["status"] != "waiting_input":
            raise Conflict("確認待ちの依頼に原本を添付してください")
        if domain not in run["domains"]:
            raise Conflict("依頼対象の業務ではありません")
        values = json.loads(binding)
        required = {"facility_hint", "week_hint"} if domain == "hospital" else {"facility_id", "month"}
        if not isinstance(values, dict) or set(values) != required or not all(isinstance(v, str) and v.strip() for v in values.values()):
            raise ValueError("原本と照合した施設・期間を明示してください")
        if run.get("task_id")=="lunch_import":
            await authorize(request.headers.get("authorization", ""),run["domains"])
            inputs=run["task_inputs"]["inputs"]
            if values != {"facility_id":str(inputs["facility_id"]),"month":inputs["month"]}:
                raise Conflict("選択した学校・月と原本の照合内容が一致しません")
        content = await file.read(20*1024*1024+1)
        if not content or len(content) > 20*1024*1024:
            raise ValueError("原本は20MB以下で指定してください")
        if domain == "hospital" and not content.startswith(b"%PDF-"):
            raise ValueError("病院注文はPDF原本が必要です")
        source_id = secrets.token_hex(16)
        folder = sandbox.path(ident) / "sources"
        folder.mkdir(exist_ok=True)
        (folder / source_id).write_bytes(content)
        (folder / (source_id+".json")).write_text(json.dumps({"domain": domain, "binding": values,
                "name": Path(file.filename or "source.pdf").name, "mime": file.content_type or "application/octet-stream"}))
        artifact = tools.artifact(ident, content, Path(file.filename or "source.pdf").name)
        store.event(ident, "source", {"source_id": source_id, "domain": domain, "binding": values, "artifact": artifact, "confirmed_by": owner})
        return {"source_id": source_id, "artifact": artifact}

    @app.get("/runs/{ident}/question-drawings/{digest}")
    def question_drawing(ident: str, digest: str, question_id: str):
        run = store.get(ident, owner)
        question = pending_question(store.events(ident)) if run['status'] == 'waiting_input' else None
        if not question or question.get('question_id') != question_id or question.get('question_map', {}).get('sha256') != digest:
            raise Conflict('現在表示中の質問の原本ではありません')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise Conflict('原本の識別情報が不正です')
        path = sandbox.path(ident) / 'question-drawings' / (digest + '.svg')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise Conflict('原本図面を確認できません')
        return FileResponse(path, media_type='image/svg+xml', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/runs/{ident}/artifacts/{artifact_id}")
    def artifact(ident: str, artifact_id: str):
        run = store.get(ident, owner)
        if run["status"] in ("rolled_back", "rollback_stop_pending"):
            raise Conflict("復元前の出力は旧版です。履歴は保持されていますが業務出力として配布しません")
        with store.read() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, ident)).fetchone()
        if not row:
            raise KeyError(artifact_id)
        path = Path(row["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise Conflict("出力ファイルが変更されています")
        return FileResponse(path, filename=row["name"], headers={"X-Content-SHA256": row["sha256"]})

    @app.get("/assistant-workflow.css")
    def workflow_styles():
        return FileResponse(Path(__file__).resolve().parents[2] / "frontend/public/assistant-workflow.css", media_type="text/css", headers={"Cache-Control":"no-cache"})

    app.mount("/mcp", mcp.streamable_http_app())
    return app


def main():
    import uvicorn
    config_path = os.environ.get("SAWA_ASSISTANT_TRIAL_CONFIG")
    config = json.loads(Path(config_path).read_text()) if config_path else {"domains": {}}
    root = Path(__file__).resolve().parents[2] / "tmp" / "agent-assistant-trial"
    app = create_app(config, root)
    # This listener cannot be enabled on 0.0.0.0 by configuration.
    uvicorn.run(app, host="127.0.0.1", port=8128)


if __name__ == "__main__":
    main()
