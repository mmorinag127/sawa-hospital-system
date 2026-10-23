"""Restart recovery uses provider history and durable receipts, never guessed replay."""
import asyncio
import json
from urllib.parse import quote

from .store import Conflict, billing_month
from .gemini import command_identity
from .conversation import record_question_receipt
from .catalog import OPERATIONS


class RecoveryPending(Conflict):
    """Remote state is known, but is still settling; history may be read again."""


class Recovery:
    def __init__(self, providers):
        self.providers = providers
        self.store = providers.store

    async def resume(self, ident):
        manager = self.providers.for_run(ident) or self.providers
        task = manager.tasks.get(ident)
        if task and not task.done():
            raise Conflict('既に処理中です')
        async with manager.tools.lock(ident):
            run = self.store.get(ident)
            if run['status'] != 'reconciliation_required':
                raise Conflict('結果照合が必要な依頼だけ再開できます')
            if run['month'] != billing_month() or run['spent'] >= run['cap'] or run['reserved'] <= 0:
                raise Conflict('実行月・利用上限・予約額を確認してください')
            if not run['session_id']:
                raise Conflict('接続先の実行IDが未確定です。再送せず予約額を保持します')
            if manager.tools.authorization_validator:
                await manager.tools.authorization_validator(run)
            # A provider call can outlive a process. Never infer that an unfinished
            # mutation failed merely because this process no longer owns its task.
            with self.store.read() as db:
                pending = list(db.execute("SELECT id,request FROM commands WHERE run_id=? AND status!='done'", (ident,)))
            safe_waits = [row for row in pending if json.loads(row['request']).get('name') in
                          ('wait_for_task_decision', 'wait_for_native_job')]
            safe_reads = [row for row in pending if
                          (operation := OPERATIONS.get(json.loads(row['request']).get('name'))) and operation.method == 'GET']
            if len(pending) != len(safe_waits) + len(safe_reads):
                raise Conflict('実行結果が未確定の命令があります。業務更新を再送せず履歴を保持します')
            directory = manager.sandbox.path(ident)
            if manager.sandbox.durable_store is not None:
                manager.sandbox.durable_store.require_working_copy(ident)
            for domain in run['domains']:
                if not (directory / 'data' / domain / 'database.sqlite3').is_file():
                    raise Conflict('保存済みの業務データがありません。初期データへの置換は行いません')
            if run['provider'] == 'gemini':
                plan = await self.gemini(manager, run)
            else:
                plan = await self.claude(manager, run)
            current = self.store.get(ident)
            if current['generation'] != run['generation'] or current['status'] != 'reconciliation_required':
                raise Conflict('照合中に依頼が停止または変更されました')
            if current['spent'] >= current['cap'] or current['reserved'] <= 0:
                raise Conflict('照合した利用料金が実行上限に達しています。処理を再開しません')
            if plan['waiting']:
                name, result = plan['question']
                from .business_contracts import owned
                replies = [event['payload'] for event in self.store.events(ident) if event['kind'] == 'operator_reply' and event['payload'].get('question_id') == result.get('command_id')]
                if owned(run) and replies:
                    # Provider history proves this tool call remains unanswered.
                    # Reuse the already accepted business answer, never ask again.
                    self.store.update(ident, expected_generation=run['generation'], status='running')
                    self.store.event(ident, 'business_answer_recovery', {'question_id': result['command_id']})
                    await manager.follow(ident, {'type': 'user.custom_tool_result', 'custom_tool_use_id': result['command_id'], 'content': [{'type': 'text', 'text': json.dumps({'previous_result': result, 'operator_reply': replies[-1]['message']}, ensure_ascii=False)}]})
                    return self.store.get(ident)
                record_question_receipt(self.store, ident, name, result)
                self.store.update(ident, expected_generation=run['generation'], status='waiting_input')
                self.store.event(ident, 'recovery', {'status': 'waiting_input', 'generation': run['generation']})
                return self.store.get(ident)
            await manager.sandbox.start(run)
            current = self.store.get(ident)
            if current['generation'] != run['generation'] or current['status'] != 'reconciliation_required':
                await manager.sandbox.stop(ident, expected_generation=run['generation'])
                raise Conflict('再開前に依頼が停止または変更されました')
            with self.store.tx() as db:
                for row in safe_waits:
                    db.execute("UPDATE commands SET status='recoverable_wait' WHERE run_id=? AND id=? AND status!='done'", (ident, row['id']))
                for row in safe_reads:
                    db.execute("UPDATE commands SET status='recoverable_read' WHERE run_id=? AND id=? AND status!='done'", (ident, row['id']))
            self.store.update(ident, expected_generation=run['generation'], status='running')
            self.store.event(ident, 'recovery', {'status': 'running', 'generation': run['generation']})
            if run['provider'] == 'gemini':
                manager.checkpoint(ident, plan['state'])
                manager.tasks[ident] = asyncio.create_task(manager.consume_gemini(ident, run['generation'], plan['state'], None))
            else:
                manager.tasks[ident] = asyncio.create_task(manager.consume(ident, plan['stream'], replay=plan['replay']))
            return self.store.get(ident)

    def waiting(self, ident, command):
        events = self.store.events(ident)
        from .business_contracts import owned
        if owned(self.store.get(ident)) and any(event['kind'] == 'operator_reply' and event['payload'].get('question_id') == command for event in events):
            # The caller has proved this exact call is still unanswered remotely.
            # Its local answer may have been saved after the waiting event.
            return any(event['kind'] == 'operator_wait' and event['payload']['command_id'] == command for event in events)
        last_reply = max((e['id'] for e in events if e['kind'] == 'operator_reply'), default=0)
        return any(e['id'] > last_reply and e['kind'] == 'operator_wait' and
                   e['payload']['command_id'] == command for e in events)

    def verify_receipt(self, ident, command, name, arguments):
        # Match the same typed defaults used by the MCP wrappers, rather than
        # comparing the model's abbreviated input with the expanded receipt.
        arguments = dict(arguments)
        if 'parameters' in arguments:
            from .tools import Arguments
            arguments['parameters'] = Arguments.model_validate(arguments['parameters']).model_dump()
        if name == 'ask_operator':
            arguments['options'] = arguments.get('options') or []
        if name == 'ask_lunch_process_question':
            arguments.setdefault('restart', False)
        with self.store.read() as db:
            row = db.execute('SELECT request,status,result FROM commands WHERE run_id=? AND id=?', (ident, command)).fetchone()
        if not row or row['status'] != 'done' or json.loads(row['request']) != {'name': name, 'arguments': arguments}:
            raise Conflict('保存済み質問の命令・結果を照合できません')
        result = json.loads(row['result'])
        if not (result.get('waiting_input') or result.get('needs_operator_confirmation')):
            raise Conflict('質問待ちの結果が一致しません')
        return result

    async def gemini(self, manager, run):
        ident = run['id']
        if manager.unsettled(ident):
            raise Conflict('Geminiの送信結果が未確定です。再送せず予約額を保持します')
        value = await manager.request('GET', '/' + quote(run['session_id'], safe=''))
        if value.get('id') != run['session_id']:
            raise Conflict('接続先の実行IDが一致しません')
        if value.get('status') in ('in_progress', 'queued'):
            raise RecoveryPending('接続先の処理を照合中です')
        if value.get('status') not in ('requires_action', 'completed'):
            raise Conflict('接続先の結果を確定できません。保存済み内容を管理者が確認します')
        manager.record_usage(ident, value)
        calls = [s for s in value.get('steps', []) if s.get('type') == 'function_call']
        if not calls:
            raise Conflict('再開対象の命令・質問がありません。結果照合の状態を保持します')
        if len({c['id'] for c in calls}) != len(calls):
            raise Conflict('接続先の命令IDが重複しています')
        state = manager.state(ident)
        if state and state['interaction'] == value['id']:
            if state['calls'] != calls:
                raise Conflict('保存済み命令と接続先の命令が一致しません')
            if type(state.get('index')) is not int or not 0 <= state['index'] <= len(calls) or len(state.get('results', [])) != state['index']:
                raise Conflict('保存済みの処理位置を照合できません')
        else:
            state = {'interaction': value['id'], 'calls': calls, 'results': [], 'index': 0, 'pending': None}
        if state.get('pending'):
            if state['index'] >= len(calls):
                raise Conflict('質問の処理位置が不正です')
            call = calls[state['index']]
            command = command_identity(value['id'], call['id'])
            if command != state['pending'] or not self.waiting(ident, command):
                raise Conflict('保存済みの質問と接続先の命令が一致しません')
            result = self.verify_receipt(ident, command, call['name'], call['arguments'])
            return {'waiting': True, 'question': (call['name'], result)}
        return {'waiting': False, 'state': state}

    async def claude(self, manager, run):
        api = manager.api()
        session = await api.retrieve(run['session_id'])
        if getattr(session, 'id', None) != run['session_id']:
            raise Conflict('ClaudeのセッションIDが一致しません')
        if session.status != 'idle':
            if session.status == 'terminated':
                raise Conflict('Claudeの実行は終了しています。保存済み内容を管理者が確認します')
            raise RecoveryPending('Claudeの処理を照合中です')
        if session.usage is None:
            raise RecoveryPending('Claudeの利用料金が未確定です。予約額を保持します')
        self.store.usage(run['id'], session.usage.model_dump(mode='json'))
        history = [e async for e in api.events.list(run['session_id'], order='asc')]
        values = [e.model_dump(mode='json') for e in history]
        sent = {e['custom_tool_use_id'] for e in values if e['type'] == 'user.custom_tool_result'}
        calls = [e for e in values if e['type'] == 'agent.custom_tool_use' and e['id'] not in sent]
        if len(calls) > 1:
            raise Conflict('未応答の命令が複数あります。接続先の履歴を確認してください')
        if not calls:
            raise Conflict('再開対象の命令がありません。未確定の送信を再送せず履歴を保持します')
        if self.waiting(run['id'], calls[0]['id']):
            result = self.verify_receipt(run['id'], calls[0]['id'], calls[0]['name'], calls[0]['input'])
            return {'waiting': True, 'question': (calls[0]['name'], result)}
        class RecoveredStream:
            async def __aenter__(self):
                self.future = await api.events.stream(run['session_id'])
                await self.future.__aenter__()
                return self

            async def __aexit__(self, *args):
                return await self.future.__aexit__(*args)

            async def __aiter__(self):
                pending_ids = {e['id'] for e in calls}
                for event in history:
                    if event.id in pending_ids:
                        yield event
                async for event in self.future:
                    yield event

        return {'waiting': False, 'stream': RecoveredStream(), 'replay': {e['id'] for e in calls}}
