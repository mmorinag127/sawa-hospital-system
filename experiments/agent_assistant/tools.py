import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .catalog import OPERATIONS, build_path, public_intake_data
from .conversation import question_result
from .store import Conflict
from .fax_workflow import FaxWorkflow
from .task_flow import TaskFlow
from .tasks import TASKS
from .presentation import EXPORT_LABELS


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: dict[str, str | int] = Field(default_factory=dict)
    query: dict = Field(default_factory=dict)
    body: dict = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    source_id: str | None = None
    reason: str = Field(default="", max_length=2000)


class Tools:
    def __init__(self, store, sandbox):
        self.store, self.sandbox = store, sandbox
        self.locks = {}
        self.workflow_locks = {}
        self.authorizations = {}
        self.authorization_validator = None
        self.faxes = FaxWorkflow(self)
        self.flow = TaskFlow(self)

    def lock(self, ident):
        return self.locks.setdefault(ident, asyncio.Lock())

    def definitions(self, domains):
        helpers = [dict(name=name,description=description,input_schema={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}) for name,description,properties in [
            ('prepare_shift_review','選択されたシフト業務を正規API・OR-Toolsで処理し、登録済み条件と勤務案を利用者の承認画面に提出する。入力・版・証跡はサーバー管理。',{}),
            ('complete_shift_work','利用者承認後、承認した勤務候補だけを確定し正規勤務表を出力・再照合して完了する。',{}),
            ('prepare_fax_review','OCR完了対象の全数量行と原本PDFを取得する。原本照合後request_fax_approvalへfax_id・summaryだけ渡す。',{'fax_id':{'type':'string'}}),
            ('complete_fax_work','全対象FAXの利用者承認後、正本の数量シートを転記せず検査・保存・確定し、正規三帳票を出力・照合して完了する。',{}),
            ('read_tool_result','同じ依頼に保存済みの完全なツール結果をJSON Pointerで読む。一覧はoffset/countでページ取得。',{'command_id':{'type':'string'},'pointer':{'type':'string'}}),
        ] if (name not in ('prepare_shift_review','complete_shift_work') or 'shift' in domains) and (name not in ('prepare_fax_review','complete_fax_work') or 'hospital' in domains)]
        result_reader=next(item for item in helpers if item['name']=='read_tool_result')['input_schema']
        result_reader['properties'].update(offset={'type':'integer','minimum':0},count={'type':'integer','minimum':1,'maximum':25})
        return helpers + [dict(name=name, description=op.description + " 試用コピー内だけで実行する。APIの拒否・blockerは解決まで保持する。"
                     + " parameters.idsはURLのパス項目だけ：" + json.dumps(re.findall(r"\{([^}]+)\}", op.path),ensure_ascii=False)
                     + ("。学校IDはparameters.query.facility_idに指定し、月指定APIではparameters.query.monthも指定する。" if op.domain=='school-lunch' else "。")
                     + (" 動線図のversionはMCPが保存状態から設定する。モデルによる指定・原本上書きは不要。" if name=='lunch_generate_route' else ""),
                     input_schema={"type": "object", "properties": {"parameters": Arguments.model_json_schema()}, "required": ["parameters"], "additionalProperties": False})
                for name, op in OPERATIONS.items() if op.domain in domains] + [
            dict(name="ask_lunch_process_question", description="日次の全献立・全食材の各工程について、一緒・別々の調理を先に確認し、別々なら食材ごとに登録設備から作業場所・使用する釜を順に質問する。工程表に不足する開始・終了時刻、担当者、汚染区分、作業段階も質問する。全回答後は変更禁止のprocess_savesを返す。そのままlunch_save_processで保存する。入力変更・利用者の訂正依頼後のみrestart=trueで再質問できる。", input_schema={"type":"object","properties":{"restart":{"type":"boolean"}},"additionalProperties":False}),
            dict(name="choose_task_layout", description="チャットで利用者が回答した図面だけを日次業務に採用する。", input_schema={"type":"object","properties":{"layout_id":{"type":"integer","minimum":1}},"required":["layout_id"],"additionalProperties":False}),
            dict(name="get_task_context", description="利用者が画面で選択した対象・期間・指定・確認回答を取得する。", input_schema={"type":"object","properties":{},"additionalProperties":False}),
            dict(name="request_task_approval", description="ネイティブGETの結果と予定する確定操作を利用者へ提示する。自分では承認できない。", input_schema={"type":"object","properties":{"summary":{"type":"string","minLength":1,"maxLength":4000},"evidence_commands":{"type":"array","items":{"type":"string"},"minItems":1},"actions":{"type":"array","items":{"type":"object","properties":{"operation":{"type":"string"},"parameters":Arguments.model_json_schema()},"required":["operation","parameters"],"additionalProperties":False}}},"required":["summary","evidence_commands","actions"],"additionalProperties":False}),
            dict(name="wait_for_task_decision", description="利用者が結果に回答するまでサーバーで待つ。停止可能。", input_schema={"type":"object","properties":{},"additionalProperties":False}),
            dict(name="wait_for_native_job", description="対象の非同期ジョブが完了するまでサーバーで待つ。モデルで反復ポーリングしない。", input_schema={"type":"object","properties":{"operation":{"type":"string","enum":["shift_get_run","lunch_get_import_job"]},"parameters":Arguments.model_json_schema()},"required":["operation","parameters"],"additionalProperties":False})
        ] + ([
            dict(name="fax_work_queue", description="選択した未処理FAX業務の対象・OCR進捗・利用者の回答を取得する。", input_schema={"type": "object", "properties": {}, "additionalProperties": False}),
            dict(name="request_fax_approval", description="保存シートと原本を利用者の確認画面へ提出する。他のFAXを続けて処理できる。", input_schema={"type": "object", "properties": {"fax_id": {"type": "string"}, "summary": {"type": "string", "minLength": 1, "maxLength": 4000}, "sheet": {"type": "object"}}, "required": ["fax_id", "summary"], "additionalProperties": False}),
            dict(name="wait_for_fax_work", description="OCR完了または利用者回答までサーバーで待つ。モデルの反復ポーリングを避ける。", input_schema={"type": "object", "properties": {}, "additionalProperties": False})
        ] if "hospital" in domains else []) + [
            dict(name="save_lunch_process_answers", description="全工程への回答後、検証済みの当日回答をサーバーからそのまま保存する。モデルが工程JSONを転記する必要はない。未回答・入力版変更・保存後不一致は拒否する。", input_schema={"type":"object","properties":{},"additionalProperties":False}),
            dict(name="save_lunch_daily_schedule",description="保存済みの当日工程表案をそのまま使い、利用者が回答した盛付時刻・原本の8個の時刻目盛・配送担当を保存する。作業行・設備・担当・工程版・保存版をモデルで転記しない。",input_schema={"type":"object","properties":{"plating_start":{"type":"string"},"plating_end":{"type":"string"},"timeline_ticks":{"type":"array","items":{"type":"string"},"minItems":8,"maxItems":8},"delivery_assignees":{"type":"array","items":{"type":"string"}}},"required":["plating_start","plating_end","timeline_ticks","delivery_assignees"],"additionalProperties":False}),
            dict(name="prepare_lunch_daily_review",description="日次の工程表・価格の保存後、回答済み工程と選択済み図面から動線図を作成する。全献立・工程・工程表・動線図・価格を再取得して人の確認画面へ提出する。図面ID・版・証跡ID・actionsをモデルで組み立てない。",input_schema={"type":"object","properties":{},"additionalProperties":False}),
            dict(name="complete_lunch_daily_work",description="人が日次三帳票を承認した後に呼ぶ。承認済みのID・版を使って正規三帳票を出力・再照合し、検査を通った場合だけ完了する。版・fingerprint・証跡IDはサーバーが管理する。",input_schema={"type":"object","properties":{},"additionalProperties":False}),
            dict(name="ask_operator", description="不足する業務入力を利用者へ質問し、依頼を確認待ちにする。値を推測しない。",
                 input_schema={"type": "object", "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 2000}, "options": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 200}, "maxItems": 8, "uniqueItems": True}}, "required": ["question"], "additionalProperties": False}),
            dict(name="read_artifact_page", description="原本PDFの指定ページを画像として読む。原本の数量照合に用いる。ページは1から。",
                 input_schema={"type": "object", "properties": {"artifact_id": {"type": "string"}, "page": {"type": "integer", "minimum": 1}}, "required": ["artifact_id", "page"], "additionalProperties": False}),
            dict(name="describe_operation", description="操作前に既存APIの現在の入力・応答スキーマを取得する。API項目を推測しない。",
                 input_schema={"type": "object", "properties": {"operation": {"type": "string"}}, "required": ["operation"], "additionalProperties": False}),
            dict(name="get_trial_jobs", description="依頼内の非同期処理の配送・実行エラーを確認する。返却済みだけでは業務完了を示さない。",
                 input_schema={"type": "object", "properties": {}, "additionalProperties": False}),
            dict(name="finish_work", description="必要な保存・出力の再取得を終えた際に実行証跡を提出する。エージェントの文章だけでは業務完了にしない。",
                 input_schema={"type": "object", "properties": {"summary": {"type": "string"}, "evidence_commands": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "artifact_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["summary", "evidence_commands", "artifact_ids"], "additionalProperties": False})]

    def artifact(self, run_id, content, name):
        ident = uuid4().hex
        folder = self.sandbox.path(run_id) / "artifacts"
        folder.mkdir(exist_ok=True)
        target = folder / ident
        target.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        with self.store.tx() as db:
            db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?)", (ident, run_id, str(target), name, digest, len(content)))
        return dict(id=ident, name=name, size=len(content), sha256=digest)

    async def execute(self, run_id, generation, name, raw, command_id):
        try:
            return await self._execute(run_id, generation, name, raw, command_id)
        finally:
            if getattr(self.sandbox, "durable_store", None) is not None:
                self.sandbox.durable_store.persist_files(run_id)

    async def _execute(self, run_id, generation, name, raw, command_id):
        from .business_contracts import require_server
        require_server(self.store.get(run_id), name)
        from .business_contracts import owned
        operation = OPERATIONS.get(name)
        if owned(self.store.get(run_id)) and operation and operation.method != 'GET':
            with self.store.read() as db:
                pending = [json.loads(row['request']) for row in db.execute("SELECT request FROM commands WHERE run_id=? AND status NOT IN ('done','recoverable_wait')", (run_id,))]
            if any(OPERATIONS.get(item['name']) and OPERATIONS[item['name']].method != 'GET' for item in pending):
                raise Conflict('保存結果を照合中です。未確定の業務更新は再実行しません')
        if name in ('prepare_shift_review','complete_shift_work','prepare_fax_review','complete_fax_work'):
            from .workflow_completion import WorkflowCompletion, OperatorWait
            workflow = WorkflowCompletion(self)
            method = {'prepare_shift_review':workflow.prepare_shift,'complete_shift_work':workflow.complete_shift,'prepare_fax_review':workflow.prepare_fax,'complete_fax_work':workflow.complete_faxes}[name]
            async with self.workflow_locks.setdefault(run_id,asyncio.Lock()):
                current=self.store.get(run_id)
                if current['generation']!=generation or current['status']!='running': raise Conflict('依頼は停止または変更されました')
                request={'name':name,'arguments':raw}
                encoded=json.dumps(request,sort_keys=True,ensure_ascii=False)
                with self.store.read() as db:
                    previous=db.execute('SELECT request,result,status FROM commands WHERE run_id=? AND id=?',(run_id,command_id)).fetchone()
                if previous:
                    if previous['request']!=encoded or previous['status']!='done': raise Conflict('命令IDの内容または結果が一致しません')
                    return json.loads(previous['result'])
                try:
                    result=await method(run_id,generation,raw,command_id)
                except OperatorWait as exc:
                    current=self.store.get(run_id)
                    if current['generation']!=generation or current['status'] not in ('running','waiting_input'): raise Conflict('依頼は停止または変更されました')
                    result=exc.result
                except (ValueError,Conflict) as exc:
                    current=self.store.get(run_id)
                    if current['generation']!=generation or current['status']!='running': raise
                    result={'error':str(exc)}
                result={**result,'command_id':command_id}
                # Children own mutation idempotency and unknown-outcome fences.
                # A wrapper is recorded only after returning, so finish_work never
                # sees its own still-running wrapper as an unresolved mutation.
                with self.store.tx() as db:
                    db.execute("INSERT INTO commands VALUES(?,?,?,'done',?)",(run_id,command_id,encoded,json.dumps(result,ensure_ascii=False)))
                self.store.event(run_id,'tool_result',{'tool':name,'result':result})
                return result
        if name in ('save_lunch_daily_schedule','prepare_lunch_daily_review','complete_lunch_daily_work'):
            from .daily_completion import DailyCompletion
            workflow=DailyCompletion(self)
            try:
                method={'save_lunch_daily_schedule':workflow.save_schedule,'prepare_lunch_daily_review':workflow.prepare,'complete_lunch_daily_work':workflow.complete}[name]
                return await method(run_id,generation,raw,command_id)
            except (ValueError,Conflict) as exc:
                current=self.store.get(run_id)
                if current['generation']!=generation or current['status']!='running': raise
                result={'error':str(exc),'command_id':command_id}
                self.store.event(run_id,'tool_result',{'tool':name,'result':result})
                return result
        if name == 'save_lunch_process_answers':
            return await self.save_lunch_answers(run_id, generation, raw, command_id)
        if name in ("wait_for_task_decision", "wait_for_native_job"):
            return await self.wait_task(run_id, generation, name, raw, command_id)
        if name == "wait_for_fax_work":
            return await self.wait_fax(run_id, generation, raw, command_id)
        async with self.lock(run_id):
            run = self.store.get(run_id)
            if run["generation"] != generation or run["status"] != "running":
                raise Conflict("この依頼は停止または変更されました。遅延した命令は実行しません")
            previous = self.store.command(run_id, command_id, {"name": name, "arguments": raw})
            if previous is not None:
                return previous
            try:
                if self.authorization_validator is not None:
                    try:
                        await self.authorization_validator(run)
                    except Conflict as exc:
                        if self.store.get(run_id)["generation"] != generation:
                            raise Conflict("依頼は停止されました")
                        self.store.update(run_id, expected_generation=generation, status="waiting_input")
                        result = {"ok": False, "needs_operator_confirmation": True, "question": str(exc), "command_id": command_id}
                        self.store.finish_command(run_id, command_id, result)
                        self.store.event(run_id, "tool_result", {"tool": name, "result": result})
                        return result
                    if self.store.get(run_id)["generation"] != generation:
                        raise Conflict("依頼は停止されました")
                if not isinstance(raw, dict):
                    raise ValueError("ツールの入力はオブジェクトが必要です")
                if name == 'read_tool_result':
                    from .tool_projection import read
                    result = read(self,run,raw)
                elif name == "ask_operator":
                    from .business_contracts import owned
                    if owned(run):
                        text = json.dumps(raw, ensure_ascii=False)
                        if re.search(r'(?:[a-z]+_(?:id|revision|version)|If-Match|APIキー|接続設定|停止を再確認|照合して再開|予算上限|（ID:)', text, re.IGNORECASE):
                            raise ValueError('内部操作・ID・版・接続設定を利用者へ質問できません。原本や登録済みデータから必要な業務情報だけを質問してください')
                    result = question_result(raw, command_id)
                    self.store.update(run_id, expected_generation=generation, status="waiting_input")
                elif name == "ask_lunch_process_question":
                    if set(raw) - {"restart"} or ("restart" in raw and type(raw["restart"]) is not bool): raise ValueError("再質問指定が不正です")
                    result = await self.flow.questions.ask(run, command_id, raw.get("restart", False))
                    if result.get("waiting_input"): self.store.update(run_id, expected_generation=generation, status="waiting_input")
                elif name == "read_artifact_page":
                    result = self.read_page(run_id, raw)
                elif name == "describe_operation":
                    result = await self.describe(run, raw)
                elif name == "get_trial_jobs":
                    if raw:
                        raise ValueError("入力項目はありません")
                    result = {"ok": True, "jobs": self.sandbox.local_jobs(run)}
                elif name == "choose_task_layout":
                    if set(raw)!={"layout_id"} or type(raw["layout_id"]) is not int: raise ValueError("図面IDが必要です")
                    result=self.flow.choose_layout(run,raw["layout_id"])
                elif name == "get_task_context":
                    if raw: raise ValueError("入力項目はありません")
                    result = self.flow.context(run)
                elif name == "request_task_approval":
                    if set(raw) != {"summary","evidence_commands","actions"}: raise ValueError("確認結果・再取得命令・確定操作が必要です")
                    result = await self.flow.submit(run, **raw)
                elif name == "fax_work_queue":
                    if raw:
                        raise ValueError("入力項目はありません")
                    result = await self.faxes.refresh(run)
                elif name == "request_fax_approval":
                    if set(raw) - {"fax_id", "summary", "sheet"} or not {"fax_id", "summary"}.issubset(raw) or not self.faxes.active(run):
                        raise ValueError("対象FAXと確認結果が必要です")
                    result = await self.faxes.submit(run, raw["fax_id"], raw["summary"], raw.get("sheet"))
                elif name == "finish_work":
                    await self.flow.finish(run, raw.get("artifact_ids", []))
                    if self.faxes.active(run):
                        await self.faxes.refresh(run)
                    checks = await self.verify_hospital_outputs(run, raw.get("artifact_ids", []))
                    result = self.finish(run_id, raw, command_id, hospital_checks=checks, expected_generation=generation)
                else:
                    result = await self.native(run, name, Arguments.model_validate(raw.get("parameters", {})), command_id=command_id)
                if name=='lunch_save_price_source' and run.get('task_id')=='lunch_daily_bundle' and result.get('ok'):
                    draft_id=result.get('data',{}).get('draft_id')
                    if not isinstance(draft_id,str) or not draft_id: raise Conflict('保存した実施価格のIDを照合できません')
                    self.store.event(run_id,'daily_price_binding',{'command_id':command_id,'draft_id':draft_id})
                result["command_id"] = command_id
                self.store.finish_command(run_id, command_id, result)
                self.store.event(run_id, "tool_result", {"tool": name, "result": {k: v for k, v in result.items() if k != "_content"}})
                return result
            except (ValueError, Conflict) as exc:
                result = {"error": str(exc), "command_id": command_id}
                self.store.finish_command(run_id, command_id, result)
                return result
            # Transport/crash failures remain executing, blocking automatic replay.

    async def save_lunch_answers(self, run_id, generation, raw, command_id):
        if raw: raise ValueError('入力項目はありません')
        async with self.lock(run_id):
            run = self.store.get(run_id)
            if run['generation'] != generation or run['status'] != 'running': raise Conflict('依頼は停止されました')
            if not self.flow.questions.active(run): raise Conflict('日次給食業務専用の操作です')
            previous = self.store.command(run_id, command_id, {'name':'save_lunch_process_answers','arguments':{}})
            if previous is not None: return previous
            try:
                state = await self.flow.questions.checked(run)
                if state['pending'] or any(len(item['answers']) != len(item['document']['steps']) for item in state['documents']):
                    raise Conflict('未回答の作業場所・使用する釜があります')
                from .lunch_execution_questions import missing
                if any(missing(item['document']) for item in state['documents']):
                    raise Conflict('工程の開始・終了時刻、担当者、汚染区分、作業段階への回答が必要です')
            except (ValueError, Conflict) as exc:
                result={'error':str(exc),'command_id':command_id}
                self.store.finish_command(run_id,command_id,result)
                return result
            saves = [self.flow.questions.payload(state,item) for item in state['documents'] if not item['saved']]
        saved=[]
        for index, parameters in enumerate(saves):
            result=await self.execute(run_id,generation,'lunch_save_process',{'parameters':parameters},command_id+':save:'+str(index))
            saved.append(result)
            if not result.get('ok'):
                result={**result,'command_id':command_id}
                self.store.finish_command(run_id,command_id,result)
                return result
        result={'ok':True,'saved':saved,'command_id':command_id}
        self.store.finish_command(run_id,command_id,result)
        self.store.event(run_id,'tool_result',{'tool':'save_lunch_process_answers','result':result})
        return result
    async def wait_task(self, run_id, generation, name, raw, command_id):
        async with self.lock(run_id):
            run = self.store.get(run_id)
            if not self.flow.active(run): raise ValueError("選択式業務の待機専用です")
            if run["generation"] != generation or run["status"] != "running": raise Conflict("依頼は停止されました")
            if name == "wait_for_task_decision" and raw: raise ValueError("入力項目はありません")
            if name == "wait_for_native_job" and (set(raw) != {"operation","parameters"} or raw["operation"] not in ("shift_get_run","lunch_get_import_job")):
                raise ValueError("待機できる対象ジョブではありません")
            previous = self.store.command(run_id, command_id, {"name":name,"arguments":raw})
            if previous is not None: return previous
        while True:
            async with self.lock(run_id):
                run = self.store.get(run_id)
                if run["generation"] != generation or run["status"] != "running": raise Conflict("依頼は停止されました")
                if self.authorization_validator is not None:
                    try: await self.authorization_validator(run)
                    except Conflict as exc:
                        if self.store.get(run_id)["generation"] != generation: raise Conflict("依頼は停止されました")
                        self.store.update(run_id,expected_generation=generation,status="waiting_input")
                        result={"ok":False,"needs_operator_confirmation":True,"question":str(exc),"command_id":command_id}
                        self.store.finish_command(run_id,command_id,result)
                        return result
                if name == "wait_for_task_decision":
                    review=self.flow.review(run_id)
                    if not review: raise ValueError("確認結果を先に提出してください")
                    ready=review["state"] != "awaiting_approval"
                    result={"ok":True,"review_id":review["id"],"state":review["state"],"decision":review["decision"]}
                else:
                    args=Arguments.model_validate(raw["parameters"])
                    await self.flow.scoped(run,raw["operation"],args,inventory=True)
                    data=await self.flow.read(run,raw["operation"],raw["parameters"])
                    status=data.get("status")
                    if not isinstance(status,str): raise Conflict("ジョブの実行状態を取得できません")
                    ready=status not in ("pending","queued","preparing","dispatching","running","processing","retry_wait")
                    result={"ok":True,"data":data}
                if self.store.get(run_id)["generation"] != generation: raise Conflict("依頼は停止されました")
                if ready:
                    result["command_id"]=command_id
                    self.store.finish_command(run_id,command_id,result)
                    self.store.event(run_id,"tool_result",{"tool":name,"result":result})
                    return result
            await asyncio.sleep(1)

    async def wait_fax(self, run_id, generation, raw, command_id):
        # Do not hold the write lock while sleeping: human answers and stop must
        # remain possible while a provider custom tool is waiting.
        async with self.lock(run_id):
            run = self.store.get(run_id)
            if raw or not self.faxes.active(run):
                raise ValueError("未処理FAX業務の待機専用です")
            if run["generation"] != generation or run["status"] != "running":
                raise Conflict("依頼は停止されました")
            previous = self.store.command(run_id, command_id, {"name": "wait_for_fax_work", "arguments": raw})
            if previous is not None:
                return previous
        initial = None
        while True:
            async with self.lock(run_id):
                run = self.store.get(run_id)
                if run["generation"] != generation or run["status"] != "running":
                    raise Conflict("依頼は停止されました")
                if self.authorization_validator is not None:
                    try:
                        await self.authorization_validator(run)
                    except Conflict as exc:
                        if self.store.get(run_id)["generation"] != generation:
                            raise Conflict("依頼は停止されました")
                        self.store.update(run_id, expected_generation=generation, status="waiting_input")
                        result = {"ok": False, "needs_operator_confirmation": True, "question": str(exc), "command_id": command_id}
                        self.store.finish_command(run_id, command_id, result)
                        return result
                if self.store.get(run_id)["generation"] != generation:
                    raise Conflict("依頼は停止されました")
                result = await self.faxes.refresh(run)
                if self.store.get(run_id)["generation"] != generation:
                    raise Conflict("依頼は停止されました")
                marker = json.dumps(result, sort_keys=True, ensure_ascii=False)
                actionable = not result["items"] or any(x["state"] in ("ready", "approved", "needs_correction", "pending", "blocked") for x in result["items"]) or all(x["state"] == "completed" for x in result["items"])
                if actionable or (initial is not None and marker != initial):
                    result["command_id"] = command_id
                    self.store.finish_command(run_id, command_id, result)
                    self.store.event(run_id, "tool_result", {"tool": "wait_for_fax_work", "result": result})
                    return result
                initial = marker
            await asyncio.sleep(1)

    async def describe(self, run, args):
        if set(args) != {"operation"} or not isinstance(args["operation"], str):
            raise ValueError("操作名が必要です")
        operation = OPERATIONS.get(args["operation"])
        if operation is None or operation.domain not in run["domains"]:
            raise ValueError("許可されていない業務操作です")
        response = await self.sandbox.request(run, operation.domain, "GET", "/openapi.json",
                                             authorization=self.authorizations.get(run["id"]))
        if not response.is_success:
            raise Conflict("業務APIのスキーマを取得できません")
        document = response.json()
        canonical = re.sub(r"\{[^}]+\}", "{}", operation.path)
        paths = [value for key, value in document["paths"].items() if re.sub(r"\{[^}]+\}", "{}", key) == canonical]
        if len(paths) != 1 or operation.method.lower() not in paths[0]:
            raise Conflict("操作と既存APIの契約が一致しません")
        schema = paths[0][operation.method.lower()]
        components = {}
        def collect(value):
            if isinstance(value, dict):
                reference = value.get("$ref", "")
                if reference.startswith("#/components/schemas/"):
                    name = reference.split("/")[-1]
                    if name not in components:
                        components[name] = document["components"]["schemas"][name]
                        collect(components[name])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(schema)
        if args['operation'] in ('shift_preflight','shift_solve') and run.get('task_id') in ('shift_next_month','shift_wishes','shift_absence'):
            reference=schema['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
            public=components[reference]
            for key in ('start_date','end_date_exclusive'): public.get('properties',{}).pop(key,None)
            public['required']=[key for key in public.get('required',[]) if key not in ('start_date','end_date_exclusive')]
            public['properties']['target_month']['const']=run['task_inputs']['inputs']['month']
        if args['operation']=='lunch_save_price_source' and 'PriceTableCostItemInput' in components:
            components['PriceTableCostItemInput']['properties']['status'].update(enum=['confirmed','unconfirmed'],description='人が原価を確認済みならconfirmed。未確認ならunconfirmed。日本語の表示名は送らない。')
            cost_schema=components['PriceTableCostItemInput']
            prices=('unit_price_tax_included','unit_price_tax_excluded')
            for field in prices:
                cost_schema['properties'][field]['description']='税込単価・税抜単価は回答された片方だけ指定する。同じ金額でも両方を送らない。'
            cost_schema.setdefault('allOf',[]).append({'not':{'required':list(prices),'properties':{field:{'not':{'type':'null'}} for field in prices}}})
        if args['operation']=='lunch_generate_route':
            reference=schema['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
            public=components[reference]
            public['required']=[key for key in public.get('required',[]) if key not in ('version','replace_confirmed_source')]
            for key in ('version','replace_confirmed_source'): public.get('properties',{}).pop(key,None)
        wrapper = {"ids": re.findall(r"\{([^}]+)\}", operation.path),
                   "query": "native schema parameters with in=query", "body": "native schema requestBody",
                   "note": "Use wrapper ids names above even when native OpenAPI uses a different path variable name."}
        if operation.domain=='school-lunch':
            wrapper['required_query'] = {"facility_id": run.get('task_inputs',{}).get('inputs',{}).get('facility_id')}
            wrapper['note'] += " School ID belongs in parameters.query.facility_id, never ids. Use selected month for native month query."
        if args['operation']=='lunch_generate_route':
            wrapper['managed_fields']={'body.version':'MCPが保存状態から取得・固定する。入力不要。競合時の自動更新・上書きは行わない。'}
        return {"ok": True, "operation": args["operation"], "path": operation.path, "wrapper_parameters": wrapper,
                "method": operation.method, "schema": schema, "components": {"schemas": components}}

    async def native(self, run, name, args, *, command_id=None):
        op = OPERATIONS.get(name)
        if op is None or op.domain not in run["domains"]:
            raise ValueError("許可されていない業務操作です")
        if op.versioned and args.expected_revision is None:
            raise ValueError("現在のworkflow revisionが必要です")
        if op.method != "GET" and not args.reason.strip():
            raise ValueError("変更理由と原本・指示の根拠を指定してください")
        if any(key in args.body for key in ("skip_ocr", "force")):
            raise ValueError("認識省略・重複強制取込は禁止です")
        if name=='lunch_save_price_source':
            costs=args.body.get('cost_items',[])
            if not isinstance(costs,list) or any(not isinstance(item,dict) or item.get('status') not in ('confirmed','unconfirmed') for item in costs):
                raise ValueError('cost_items.statusは確認済みならconfirmed、未確認ならunconfirmedを指定してください。利用者の原価確認回答は変更しません。')
            if any(item.get('unit_price_tax_included') is not None and item.get('unit_price_tax_excluded') is not None for item in costs):
                raise ValueError('cost_itemsは税込単価・税抜単価の片方だけ指定してください。同じ金額でも両方は送れません。回答済みの金額・税率は変更せず、形式だけ修正してください。')
        path = build_path(op, args.ids)
        await self.flow.gate(run, name, args)
        await self.faxes.gate(run, name, args)
        if name=='lunch_generate_route':
            from .route_generation import bind_version
            args=await bind_version(self,run,args)
        if "//" in path or ".." in path:
            raise ValueError("invalid path")
        if op.versioned:
            # Pass the exact observed revision; never retry a conflict using a new one.
            query = dict(args.query)
        else:
            query = args.query
        files = None
        if op.file:
            if not args.source_id or not re.fullmatch(r"[0-9a-f]{32}", args.source_id):
                raise ValueError("利用者が施設・期間を確認した添付原本が必要です")
            source = self.sandbox.path(run["id"]) / "sources" / args.source_id
            metadata = json.loads(source.with_suffix(".json").read_text())
            if metadata["domain"] != op.domain:
                raise ValueError("原本と業務が一致しません")
            for key, value in metadata["binding"].items():
                binding_input = args.body if op.domain == "hospital" else args.query
                if str(binding_input.get(key, "")) != value:
                    raise ValueError("原本の確認済み施設・期間と取込指定が一致しません")
            field = "pdf_file" if op.domain == "hospital" else "file"
            files = {field: (metadata["name"], source.read_bytes(), metadata["mime"])}
        if name == 'shift_solve':
            await self.flow.capture_shift_reference(run)
        response = await self.sandbox.request(
            run, op.domain, op.method, path, params=query, body=args.body or None,
            authorization=self.authorizations.get(run["id"]), files=files,
            **({"revision": args.expected_revision} if op.versioned else {}))
        if not response.is_success:
            from .business_contracts import owned
            if owned(run):
                if response.status_code >= 500 and op.method != 'GET':
                    # The server might have committed before sending this response.
                    # Keep the command pending so no model or retry can repeat it.
                    raise RuntimeError('業務更新の応答が未確定です')
                return {'ok': False, 'status': response.status_code, 'detail': response.text[:12000],
                        'input_validation_failed': response.status_code in (400, 422),
                        'next_step': '登録済みの内容と入力形式を確認する。業務上の不足だけを具体的な選択肢で質問する。接続・権限・競合の復旧操作を利用者に求めない。'}
            known_price_format_error=(name=='lunch_get_price_result' and response.status_code==400
                and 'must not define both tax-included and tax-excluded prices' in response.text)
            if response.status_code == 422 or known_price_format_error:
                return {"ok":False,"status":response.status_code,"detail":response.text[:12000],
                        "input_validation_failed":True,"next_step":"describe_operationで現行の入力形式を確認する。利用者の選択・確認済み回答だけで修正し、不足する業務情報はask_operatorで質問する。競合や正本を変更して回避しない。"}
            self.store.update(run["id"], expected_generation=run['generation'], status="waiting_input")
            return {"ok": False, "status": response.status_code, "detail": response.text[:12000],
                    "needs_operator_confirmation": True,
                    "question": "業務APIが処理を拒否しました。履歴の理由を確認し、対象・入力・設定の修正内容を回答してください。競合を自動上書きしません。"}
        from .business_contracts import owned
        if owned(run) and op.method != "GET" and command_id:
            with self.store.read() as db:
                request=db.execute('SELECT request FROM commands WHERE run_id=? AND id=?',(run['id'],command_id)).fetchone()
            if not request or 'application/json' not in response.headers.get('content-type',''):
                raise Conflict('業務更新の保存結果を照合できません')
            # CloudStore commits the working-copy snapshot and ledger together.
            # Keep the response before any projection/recheck that could disconnect.
            self.store.event(run['id'],'native_response_receipt',{'command_id':command_id,'generation':run['generation'],
                'name':name,'request_sha256':hashlib.sha256(request['request'].encode()).hexdigest(),
                'parameters':args.model_dump(mode='json'),'status':response.status_code,'data':response.json()})
        return await self.native_result(run,name,args,response)

    async def native_result(self,run,name,args,response):
        op=OPERATIONS[name]
        if op.method != "GET":
            await self.flow.after(run, name, args, response.json() if "application/json" in response.headers.get("content-type", "") else {})
            await self.faxes.after_write(run, name, args, response.json() if "application/json" in response.headers.get("content-type", "") else {})
        if "application/json" in response.headers.get("content-type", ""):
            from .daily_scope import selected
            return {"ok": True, "status": response.status_code, "data": selected(run,name,public_intake_data(name,response.json())),
                    "verification_required": op.method != "GET"}
        await self.flow.verify_output(run, name)
        if len(response.content) == 0:
            raise Conflict("空の出力を受領しました")
        disposition = response.headers.get("content-disposition", "")
        filename = re.search(r'filename="([^"]+)"', disposition)
        extension = {"application/pdf": ".pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx", "text/csv": ".csv"}.get(response.headers.get("content-type", "").split(";")[0], "")
        label = EXPORT_LABELS.get(name)
        target = args.query.get("order_id") or args.query.get("candidate_id") or args.ids.get("schedule_id") or args.ids.get("diagram_id") or args.query.get("month") or args.ids.get("draft_id")
        display_name = (label+("-"+str(target) if target is not None else "")+extension) if label else (Path(filename.group(1)).name if filename else name+extension)
        artifact = self.artifact(run["id"], response.content, display_name)
        return {"ok": True, "status": response.status_code, "artifact": artifact}

    def read_page(self, run_id, args):
        import fitz
        if set(args) != {"artifact_id", "page"} or type(args["page"]) is not int or args["page"] < 1:
            raise ValueError("原本IDと1以上のページ番号が必要です")
        with self.store.read() as db:
            artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (args["artifact_id"], run_id)).fetchone()
        if not artifact:
            raise ValueError("この依頼の原本が見つかりません")
        content = Path(artifact["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise Conflict("原本が変更されています")
        with fitz.open(stream=content, filetype="pdf") as document:
            if args["page"] > len(document):
                raise ValueError("ページがありません")
            page = document[args["page"]-1]
            if page.rect.width * page.rect.height * 2.25 > 16_000_000:
                raise ValueError("原本ページが画像化上限を超えています")
            pixels = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).tobytes("png")
        return {"ok": True, "artifact_id": args["artifact_id"], "page": args["page"],
                "_content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(pixels).decode()}}]}

    def hospital_output_commands(self, run_id, artifact_ids):
        kinds = {"hospital_export_labels": "labels", "hospital_export_delivery_notes": "delivery",
                 "hospital_export_aggregate": "aggregate"}
        with self.store.read() as db:
            rows = db.execute("SELECT request,result FROM commands WHERE run_id=? AND status='done'", (run_id,)).fetchall()
        outputs = {}
        for row in rows:
            request, result = json.loads(row["request"]), json.loads(row["result"])
            artifact = result.get("artifact") or {}
            if request["name"] in kinds and artifact.get("id") in artifact_ids and result.get("ok"):
                order_id = request["arguments"].get("parameters", {}).get("query", {}).get("order_id")
                if not order_id:
                    raise Conflict("帳票の対象注文を照合できません")
                outputs[artifact["id"]] = {"kind": kinds[request["name"]], "order_id": str(order_id), "sha256": artifact["sha256"]}
        return outputs

    async def verify_hospital_outputs(self, run, artifact_ids):
        checks = {}
        for artifact_id, target in self.hospital_output_commands(run["id"], artifact_ids).items():
            with self.store.read() as db:
                row = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run["id"])).fetchone()
            if row is None:
                raise Conflict("検査対象の帳票がありません")
            content = Path(row["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != target["sha256"] or len(content) != row["size"]:
                raise Conflict("帳票が変更されています")
            response = await self.sandbox.request(run, "hospital", "POST", "/outputs/validate-received",
                body={"order_id": target["order_id"], "kind": target["kind"]},
                files={"artifact_file": (row["name"], content, "application/octet-stream")},
                authorization=self.authorizations.get(run["id"]))
            if not response.is_success:
                raise Conflict("帳票の内容検査に失敗しました。完了できません：" + response.text[:2000])
            check = response.json()
            if check.get("valid") is not True or any(check.get(key) != value for key, value in target.items()) or not check.get("confirmed_snapshot_id") or type(check.get("revision")) is not int:
                raise Conflict("帳票の検査結果と注文・ファイルが一致しません")
            checks[artifact_id] = check
        # Recheck after all three validations; concurrent changes invalidate the batch.
        for check in checks.values():
            wf = (await self.faxes.get(run, f"/orders/{check['order_id']}/workflow-v2")).json()
            if wf.get("state") != "confirmed" or wf.get("revision") != check["revision"] or wf.get("confirmed_snapshot_id") != check["confirmed_snapshot_id"]:
                raise Conflict("帳票検査中に注文が変わりました。再出力が必要です")
        return checks

    def finish(self, run_id, args, current_command_id="", *, hospital_checks=None, expected_generation=None):
        if set(args) != {"summary", "evidence_commands", "artifact_ids"} or not args["evidence_commands"]:
            raise ValueError("完了には保存後の再取得・出力照合の命令IDが必要です")
        run = self.store.get(run_id)
        if run["status"] != "running" or (expected_generation is not None and run["generation"] != expected_generation):
            raise Conflict("この依頼は停止または変更されました。完了にはできません")
        if any(job["state"] != "returned" for job in self.sandbox.local_jobs(run)):
            raise Conflict("未完了または失敗した非同期実行があります")
        with self.store.read() as db:
            reads = []
            for ident in args["evidence_commands"]:
                row = db.execute("SELECT rowid,* FROM commands WHERE run_id=? AND id=?", (run_id, ident)).fetchone()
                if not row or row["status"] != "done" or not json.loads(row["result"]).get("ok"):
                    raise Conflict("未検証または失敗した命令が完了証跡に含まれます")
                request = json.loads(row["request"])
                operation = OPERATIONS.get(request["name"])
                if not operation or operation.method != "GET":
                    raise Conflict("完了証跡には実APIからの再取得結果が必要です")
                result = json.loads(row["result"])
                data = result.get("data") or {}
                if self.unresolved_evidence(request["name"], data) and not (run.get("task_id") == "shift_check" and request["name"] in ("shift_get_run","shift_get_validation")):
                    raise Conflict("未解決のblockerまたは古い入力が残っています")
                reads.append((request, result, row["rowid"]))
            # A successful unrelated GET cannot prove that an update was saved.
            mutations = db.execute("SELECT rowid,* FROM commands WHERE run_id=? AND status='done'", (run_id,)).fetchall()
            for mutation in mutations:
                request = json.loads(mutation["request"])
                op = OPERATIONS.get(request["name"])
                result = json.loads(mutation["result"])
                if not op or op.method == "GET" or result.get("ok") is not True:
                    continue
                target = request["arguments"].get("parameters", {}).get("ids", {})
                matched = False
                for read, read_result, read_rowid in reads:
                    read_op = OPERATIONS[read["name"]]
                    read_target = read["arguments"].get("parameters", {}).get("ids", {})
                    if read_op.domain != op.domain or not self.covers(op, target, read_op, read_target, read_result.get("data")):
                        continue
                    if read_rowid > mutation["rowid"]:
                        matched = True
                if not matched:
                    raise Conflict("変更対象を保存後に再取得した証跡がありません")
            for ident in args["artifact_ids"]:
                artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=?", (ident, run_id)).fetchone()
                if not artifact:
                    raise Conflict("成果物がありません")
                content = Path(artifact["path"]).read_bytes()
                if len(content) != artifact["size"] or hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                    raise Conflict("成果物が変更されています")
            if db.execute("SELECT 1 FROM commands WHERE run_id=? AND status!='done' AND id!=?", (run_id, current_command_id)).fetchone():
                raise Conflict("結果未確定の命令が残っています")
        # Finish is a submitted claim; UI always shows actual receipts for review.
        self.faxes.finish_gate(run_id, args["artifact_ids"])
        required = self.hospital_output_commands(run_id, args["artifact_ids"])
        if required and (hospital_checks is None or set(hospital_checks) != set(required) or any(
                hospital_checks[ident].get("valid") is not True or any(hospital_checks[ident].get(key) != value for key, value in target.items())
                for ident, target in required.items())):
            raise Conflict("帳票の内容検査が完了していません")
        if required:
            self.store.event(run_id, "hospital_output_validation", {"artifacts": hospital_checks})
        self.store.event(run_id, "completion_evidence", args)
        self.store.update(run_id, expected_generation=run['generation'], status="completed")
        return {"ok": True, "summary": args["summary"], "scope": "isolated_trial_only"}

    @staticmethod
    def covers(operation, target, read_operation, read_target, data):
        # Entity-wide reads (e.g. candidate validation) cover its child cell IDs.
        primary = next((key for key in ("uploaded_pdf_id", "order_id", "candidate_id", "run_id", "menu_id", "schedule_id", "diagram_id", "draft_id", "staff_id", "constraint_id", "assignment_id") if key in target), None)
        if primary is not None and read_target.get(primary) == target[primary]:
            return True
        root = operation.path.split("/{", 1)[0]
        read_root = read_operation.path.split("/{", 1)[0]
        if primary == "assignment_id" and read_root == "/solve-runs" and isinstance(data, dict):
            return any(str(row.get("id")) == str(target[primary]) for row in data.get("assignments", []) if isinstance(row, dict))
        roots = {"/solve": "/solve-runs", "/ingest/upload": "/ingest/uploads",
                 "/rule-assistant/apply": "/optimization-settings", "/rule-assistant/candidates": "/optimization-settings",
                 "/rule-preflight": "/optimization-settings", "/api/school-lunch/route-diagrams/generate": "/api/school-lunch/route-diagrams",
                 "/api/school-lunch/cooking-arrangements/confirm": "/api/school-lunch/menu-executions",
                 "/api/school-lunch/cooking-arrangements/extract": "/api/school-lunch/cooking-arrangements/documents"}
        if read_root != roots.get(root, root):
            return False
        if primary is None:
            return True
        def contains(value):
            if isinstance(value, dict):
                if any(str(value.get(key, "")) == str(target[primary]) for key in (primary, "id")):
                    return True
                return any(contains(child) for child in value.values())
            return isinstance(value, list) and any(contains(child) for child in value)
        return contains(data)

    @staticmethod
    def unresolved_evidence(operation, data):
        # The confirmed native snapshot is authoritative. Preserve the receipt,
        # including old OCR errors; ignore only its superseded job status when
        # judging the currently confirmed order, for every hospital task.
        if (operation == "hospital_get_workflow" and isinstance(data, dict)
                and data.get("state") == "confirmed" and data.get("confirmed_snapshot_id")
                and not data.get("blockers")):
            data = {key:value for key,value in data.items() if key not in ("ocr_job","common_job_status")}
        return Tools.unresolved(data)

    @staticmethod
    def unresolved(value):
        if isinstance(value, dict):
            if value.get("blockers") or value.get("blocking") is True or value.get("stale") is True:
                return True
            for key, item in value.items():
                if key in {"status", "state", "job_status", "ocr_status"} and isinstance(item, str) and item.lower() in {
                    "pending", "queued", "running", "processing", "failed", "error", "requires_review", "blocked", "stale"}:
                    return True
                if Tools.unresolved(item):
                    return True
        elif isinstance(value, list):
            return any(Tools.unresolved(item) for item in value)
        return False
