"""One MCP tool implementation shared by HTTP clients and the local Claude bridge."""
from contextvars import ContextVar
import json

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

from .catalog import OPERATIONS
from .tools import Arguments
from .tool_projection import project
from mcp.server.fastmcp.exceptions import ToolError

binding = ContextVar("trial_run_binding", default=None)


def build_mcp(tools):
    server = FastMCP("sawa-agent-trial", stateless_http=True, json_response=True,
                     streamable_http_path="/", instructions="隔離した依頼コピーでのみ操作する。原本・施設・期間と現在版を照合し、未確定値を推測しない。停止・予算・復元を迂回しない。")

    async def invoke(name, arguments, ctx):
        current = binding.get()
        if current is None:
            request = ctx.request_context.request
            current = getattr(request.state, "trial_binding", None)
        if current is None:
            raise ValueError("依頼の認証が必要です")
        run_id, generation = current
        request = ctx.request_context.request if binding.get() is None else None
        command_id = (request.headers.get("Idempotency-Key") if request is not None else None) or binding_command.get()
        if not command_id:
            raise ValueError("Idempotency-Keyが必要です")
        return project(await tools.execute(run_id, generation, name, arguments, command_id))

    def register(name, operation):
        async def execute(parameters: Arguments, ctx: Context):
            return await invoke(name, {"parameters": parameters.model_dump()}, ctx)
        server.add_tool(execute, name=name, description=operation.description,
                        annotations=ToolAnnotations(readOnlyHint=operation.method == "GET", destructiveHint=False, openWorldHint=False))

    for name, op in OPERATIONS.items():
        register(name, op)

    @server.tool()
    async def prepare_shift_review(ctx: Context):
        """選択月・登録済み条件で勤務案を作り、版と証跡を管理して利用者確認へ提出する。"""
        return await invoke('prepare_shift_review', {}, ctx)

    @server.tool()
    async def complete_shift_work(ctx: Context):
        """利用者承認後、承認候補だけを確定・正規勤務表出力・照合して完了する。"""
        return await invoke('complete_shift_work', {}, ctx)

    @server.tool()
    async def prepare_fax_review(fax_id: str, ctx: Context):
        """対象FAXの正本と原本を取得する。数量照合後、request_fax_approvalへ提出する。"""
        return await invoke('prepare_fax_review', {'fax_id': fax_id}, ctx)

    @server.tool()
    async def complete_fax_work(ctx: Context):
        """承認済みFAXを正規APIで検査・保存・確定し三帳票を照合して完了する。"""
        return await invoke('complete_fax_work', {}, ctx)

    @server.tool()
    async def read_tool_result(command_id: str, pointer: str, ctx: Context, offset: int = 0, count: int = 10):
        """この依頼の完全なツール保存結果をJSON Pointerで読み、一覧はページで読む。"""
        return await invoke('read_tool_result', {'command_id':command_id,'pointer':pointer,'offset':offset,'count':count},ctx)

    @server.tool()
    async def describe_operation(operation: str, ctx: Context):
        """操作前に対象業務APIの現行入力・応答スキーマを取得する。"""
        return await invoke("describe_operation", {"operation": operation}, ctx)

    @server.tool()
    async def get_trial_jobs(ctx: Context):
        """依頼内の非同期処理の配送・実行エラーを確認する。"""
        return await invoke("get_trial_jobs", {}, ctx)

    @server.tool()
    async def ask_lunch_process_question(ctx: Context, restart: bool = False):
        """日次業務の食材・工程ごとに一緒・別々の調理と設備を質問する。"""
        return await invoke("ask_lunch_process_question", {"restart": restart}, ctx)

    @server.tool()
    async def save_lunch_process_answers(ctx: Context):
        """検証済みの当日回答を転記せずそのまま正規APIへ保存する。"""
        return await invoke('save_lunch_process_answers', {}, ctx)

    @server.tool()
    async def save_lunch_daily_schedule(plating_start: str, plating_end: str, timeline_ticks: list[str], delivery_assignees: list[str], ctx: Context):
        """回答された盛付・時刻目盛・配送担当を保存済み工程表案へ設定する。"""
        return await invoke('save_lunch_daily_schedule',{'plating_start':plating_start,'plating_end':plating_end,'timeline_ticks':timeline_ticks,'delivery_assignees':delivery_assignees},ctx)

    @server.tool()
    async def prepare_lunch_daily_review(ctx: Context):
        """日次三帳票の保存結果を再取得し、利用者の確認へ提出する。"""
        return await invoke('prepare_lunch_daily_review',{},ctx)

    @server.tool()
    async def complete_lunch_daily_work(ctx: Context):
        """承認された日次三帳票を取得・再照合し、検査後に完了する。"""
        return await invoke('complete_lunch_daily_work',{},ctx)

    @server.tool()
    async def choose_task_layout(layout_id: int, ctx: Context):
        """利用者がチャットで選択した図面を日次作業に採用する。"""
        return await invoke("choose_task_layout", {"layout_id":layout_id}, ctx)

    @server.tool()
    async def get_task_context(ctx: Context):
        """利用者が選択した作業対象・期間・指定を取得する。"""
        return await invoke("get_task_context", {}, ctx)

    @server.tool()
    async def request_task_approval(summary: str, evidence_commands: list[str], actions: list[dict], ctx: Context):
        """実保存結果と確定予定の内容を利用者に確認してもらう。"""
        return await invoke("request_task_approval", {"summary":summary,"evidence_commands":evidence_commands,"actions":actions}, ctx)

    @server.tool()
    async def wait_for_task_decision(ctx: Context):
        """利用者回答をサーバー内で待つ。"""
        return await invoke("wait_for_task_decision", {}, ctx)

    @server.tool()
    async def wait_for_native_job(operation: str, parameters: Arguments, ctx: Context):
        """実行中ジョブの完了をサーバー内で待つ。"""
        return await invoke("wait_for_native_job", {"operation":operation,"parameters":parameters.model_dump()}, ctx)

    @server.tool()
    async def fax_work_queue(ctx: Context):
        """未処理FAX定型業務の対象と進捗を取得する。"""
        return await invoke("fax_work_queue", {}, ctx)

    @server.tool()
    async def request_fax_approval(fax_id: str, summary: str, ctx: Context, sheet: dict | None = None):
        """一件の原本・保存シートを利用者確認へ提出する。他のFAXの処理は続行する。"""
        return await invoke("request_fax_approval", {"fax_id": fax_id, "summary": summary, **({"sheet": sheet} if sheet is not None else {})}, ctx)

    @server.tool()
    async def wait_for_fax_work(ctx: Context):
        """OCR完了または利用者回答までモデルを呼ばずに待つ。"""
        return await invoke("wait_for_fax_work", {}, ctx)

    @server.tool()
    async def ask_operator(question: str, ctx: Context, options: list[str] | None = None):
        """不足する業務入力を利用者へ質問し、確認待ちにする。"""
        return await invoke("ask_operator", {"question": question, "options": options or []}, ctx)

    @server.tool()
    async def read_artifact_page(artifact_id: str, page: int, ctx: Context):
        """原本PDFの指定ページを画像として読む。ページは1から。"""
        return await invoke("read_artifact_page", {"artifact_id": artifact_id, "page": page}, ctx)

    @server.tool()
    async def finish_work(summary: str, evidence_commands: list[str], artifact_ids: list[str], ctx: Context):
        """保存後の再取得と出力照合の実行証跡を提出する。"""
        return await invoke("finish_work", {"summary": summary, "evidence_commands": evidence_commands, "artifact_ids": artifact_ids}, ctx)
    return server


binding_command = ContextVar("trial_command_binding", default=None)


async def call_local(server, tools, run_id, generation, name, arguments, command_id):
    """Private local MCP client bridge: no inbound public tunnel or cloud credentials."""
    token = binding.set((run_id, generation))
    command = binding_command.set(command_id)
    try:
        # FastMCP validates the same tool schema used by external MCP clients.
        try:
            result = await server.call_tool(name, arguments)
        except ToolError as exc:
            # Only FastMCP's pre-execution input validation is recoverable here.
            # Business transport/unknown writes must retain their original fence.
            if not str(exc).startswith('Error executing tool ' + name + ':') or 'validation error' not in str(exc):
                raise
            from pydantic import ValidationError
            cause = exc.__cause__
            if not isinstance(cause, ValidationError):
                raise
            current = tools.store.get(run_id)
            if current['generation'] != generation or current['status'] != 'running':
                from .store import Conflict
                raise Conflict('依頼は停止または変更されました')
            result = {'ok':False,'input_validation_failed':True,'executed':False,'error':str(cause),'command_id':command_id,'next_step':'提示されたツール入力形式を修正して呼び直す。parameters直下はids/query/body/expected_revision/source_id/reasonのみ。業務値を変更して回避しない。'}
            tools.store.event(run_id,'tool_input_rejected',{'tool':name,'result':result})
            return result
        if isinstance(result, dict):
            return result
        if isinstance(result, tuple):
            content, structured = result
            if structured is not None:
                return structured
        else:
            content = result
        return json.loads(next(block.text for block in content if block.type == "text"))
    finally:
        binding.reset(token)
        binding_command.reset(command)
