"""Gemini Interactions bridge. Only the same bound local MCP tools can act."""
import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import hashlib
import os
import re
from urllib.parse import quote
from uuid import uuid4

import httpx

from .managed import Managed, SYSTEM
from .mcp_server import call_local
from .store import Conflict
from .sandbox import run_blocking
from .tasks import TASKS
from .conversation import record_question_receipt

QUIET = {'requires_action', 'completed', 'failed', 'cancelled', 'incomplete'}
SETTINGS = ('GEMINI_AGENT_MODEL', 'GEMINI_AGENT_INPUT_USD_PER_MILLION', 'GEMINI_AGENT_OUTPUT_USD_PER_MILLION')


class RequestRejected(Conflict):
    """An explicit validation/auth rejection has no unknown running interaction."""


def command_identity(interaction_id, call_id):
    """Stable local receipt ID also satisfies native question-reference schemas."""
    return 'gemini_' + hashlib.sha256(json.dumps([interaction_id,call_id],separators=(',',':')).encode()).hexdigest()


def content(blocks):
    result = []
    for block in blocks:
        if block['type'] == 'text': result.append({'type': 'text', 'text': block['text']})
        elif block['type'] == 'image' and block['source']['type'] == 'base64':
            result.append({'type': 'image', 'data': block['source']['data'], 'mime_type': block['source']['media_type']})
        else: raise Conflict('Geminiへ渡せないツール結果です')
    return result


def function_result(blocks):
    # A Content[] result is the multimodal protocol, even when all blocks are text.
    # Gemini 2.5 accepts textual function results but rejects that protocol.
    if all(block['type'] == 'text' for block in blocks):
        return '\n'.join(block['text'] for block in blocks)
    return content(blocks)


class Gemini(Managed):
    def missing(self):
        missing = [key for key in SETTINGS if not os.environ.get(key)]
        if not os.environ.get('GEMINI_API_KEY'): missing.insert(0, 'GEMINI_API_KEY')
        return missing

    def pin(self, ident):
        previous = [e['payload'] for e in self.store.events(ident) if e['kind'] == 'gemini_binding']
        if previous: return previous[-1]
        if self.missing(): raise Conflict('Gemini接続設定が未完了です：' + ', '.join(self.missing()))
        model = os.environ['GEMINI_AGENT_MODEL']
        if not re.fullmatch(r'gemini-[a-zA-Z0-9._-]+', model): raise ValueError('Geminiモデル名が不正です')
        rates = [Decimal(os.environ[key]) for key in SETTINGS[1:]]
        if any(not r.is_finite() or r <= 0 for r in rates): raise ValueError('Geminiの入力・出力単価は正の数で設定してください')
        maximum = int(os.environ.get('GEMINI_AGENT_MAX_OUTPUT_TOKENS', '4096'))
        if not 1 <= maximum <= 65536: raise ValueError('Geminiの最大出力トークン数が不正です')
        value = dict(model=model, input_rate=str(rates[0]), output_rate=str(rates[1]), max_output_tokens=maximum)
        self.store.event(ident, 'gemini_binding', value)
        return value

    async def request(self, method, path='', body=None):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False)
        response = await self.client.request(method, 'https://generativelanguage.googleapis.com/v1beta/interactions' + path,
            headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']}, json=body)
        if not response.is_success:
            error = RequestRejected if response.status_code in (400, 401, 403, 404, 422) else Conflict
            raise error('Gemini APIが処理を拒否しました（HTTP ' + str(response.status_code) + '）。自動再送はしません。')
        return response.json()

    def checkpoint(self, ident, state):
        self.store.event(ident, 'gemini_state', state)

    def state(self, ident):
        return next((e['payload'] for e in reversed(self.store.events(ident)) if e['kind'] == 'gemini_state'), None)

    def current(self, ident, generation):
        run = self.store.get(ident)
        if run['generation'] != generation or run['status'] != 'running': raise Conflict('依頼は停止または変更されました')
        return run

    async def _start(self, run):
        await run_blocking(self.pin, run['id'])
        await self.sandbox.start(run)
        await run_blocking(self.store.update, run['id'], expected_generation=run['generation'], status='running')
        from .business_contracts import preparation_instruction
        text = preparation_instruction(self.tools, run)
        await self.follow(run['id'], {'type': 'user.message', 'content': [{'type': 'text', 'text': text}]})

    async def follow(self, ident, event):
        old = self.tasks.get(ident)
        if old and not old.done(): raise Conflict('既にイベントを処理中です')
        run = await run_blocking(self.store.get, ident)
        await run_blocking(self.current, ident, run['generation'])
        state = await run_blocking(self.state, ident)
        if event['type'] == 'user.custom_tool_result':
            if not state or state.get('pending') != event['custom_tool_use_id']: raise Conflict('回答するGeminiの質問が一致しません')
            call = state['calls'][state['index']]
            state['results'].append({'type': 'function_result', 'call_id': call['id'], 'name': call['name'], 'result': function_result(event['content'])})
            state['index'] += 1; state['pending'] = None
            await run_blocking(self.checkpoint, ident, state)
            inputs = None
        else:
            if state and state.get('pending'): raise Conflict('先に表示中の質問へ回答してください')
            inputs = content(event['content'])
            state = None
        self.tasks[ident] = asyncio.create_task(self.consume_gemini(ident, run['generation'], state, inputs))

    def record_usage(self, ident, interaction):
        usage = interaction.get('usage')
        keys = ('total_input_tokens', 'total_output_tokens', 'total_thought_tokens')
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys):
            raise Conflict('Geminiのトークン利用量が未確定です。予約額を保持します')
        self.store.event(ident, 'gemini_usage', {'id': interaction['id'], 'usage': usage})
        by_id = {}
        for event in self.store.events(ident):
            if event['kind'] == 'gemini_usage':
                value = event['payload']; old = by_id.get(value['id'], {})
                by_id[value['id']] = {k: max(old.get(k, 0), value['usage'][k]) for k in keys}
        pin = self.pin(ident)
        totals = {k: sum(row[k] for row in by_id.values()) for k in keys}
        amount = (Decimal(totals[keys[0]]) * Decimal(pin['input_rate']) +
                  Decimal(totals[keys[1]] + totals[keys[2]]) * Decimal(pin['output_rate'])) / 10000
        self.store.usage(ident, {**totals, 'provider': 'gemini', 'basis': 'configured_token_rates', 'estimated': True,
            'model': pin['model'], 'rates': pin, 'list_cost': {'currency': 'USD', 'amount': str(int(amount.to_integral_value(rounding=ROUND_CEILING)))}})

    def unsettled(self, ident):
        dispatches, responses = set(), set()
        for e in self.store.events(ident):
            if e['kind'] == 'gemini_dispatch': dispatches.add(e['payload']['id'])
            if e['kind'] == 'gemini_response': responses.add(e['payload']['dispatch'])
            if e['kind'] == 'gemini_rejected': responses.add(e['payload']['dispatch'])
        return dispatches - responses

    async def refresh_usage(self, ident, release=False, *, terminal_generation=None):
        if terminal_generation is None:
            terminal_generation = (await run_blocking(self.store.get, ident))['generation']
        ids = list(dict.fromkeys(e['payload']['id'] for e in await run_blocking(self.store.events, ident) if e['kind'] == 'gemini_response'))
        if not ids: raise Conflict('Geminiの実行結果が未確定です。予約額を保持します')
        for interaction_id in ids:
            value = await self.request('GET', '/' + quote(interaction_id, safe=''))
            if value.get('id') != interaction_id: raise Conflict('Gemini実行IDが一致しません')
            if release and value.get('status') not in QUIET: raise Conflict('Geminiの停止確認待ちです')
            await run_blocking(self.record_usage, ident, value)
        if release:
            if await run_blocking(self.unsettled, ident): raise Conflict('Geminiの送信結果が未確定です。再送せず予約額を保持します')
            current = await run_blocking(self.store.get, ident)
            await run_blocking(self.store.usage, ident, current['usage'], terminal=True, terminal_generation=terminal_generation)
        return value

    async def dispatch_receipt(self, ident, dispatch, body, generation):
        try:
            value = await self.request('POST', body=body)
        except RequestRejected:
            await run_blocking(self.store.event, ident, 'gemini_rejected', {'dispatch': dispatch})
            raise
        interaction_id = value.get('id')
        if not isinstance(interaction_id, str) or not interaction_id:
            raise Conflict('Geminiの実行IDがありません')
        await run_blocking(self.store.event, ident, 'gemini_response', {'id': interaction_id, 'dispatch': dispatch})
        try:
            await run_blocking(self.store.update, ident, expected_generation=generation, session_id=interaction_id)
        except Conflict:
            await run_blocking(self.store.event, ident, 'late_gemini_response', {'id': interaction_id, 'dispatch': dispatch, 'generation': generation})
        return value

    async def consume_gemini(self, ident, generation, state, inputs):
        try:
            while True:
                run = await run_blocking(self.current, ident, generation)
                if state:
                    while state['index'] < len(state['calls']):
                        await run_blocking(self.current, ident, generation)
                        call = state['calls'][state['index']]
                        command = command_identity(state['interaction'], call['id'])
                        result = await call_local(self.mcp, self.tools, ident, generation, call['name'], call['arguments'], command)
                        current = await run_blocking(self.store.get, ident)
                        if current['generation'] != generation or current['status'] in ('stopping', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending'):
                            return
                        if result.get('waiting_input') or result.get('needs_operator_confirmation'):
                            await run_blocking(self.store.update, ident, expected_generation=generation, status='waiting_input')
                            await run_blocking(record_question_receipt, self.store, ident, call['name'], result)
                            state['pending'] = command; await run_blocking(self.checkpoint, ident, state)
                            await run_blocking(self.store.event, ident, 'operator_wait', {'command_id': command, 'result': result})
                            return
                        business = getattr(self.tools, 'business', None)
                        if business and await business.pause_model(ident, command, result):
                            state['pending'] = command
                            await run_blocking(self.checkpoint, ident, state)
                            return
                        blocks = result.get('_content') or [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]
                        state['results'].append({'type': 'function_result', 'call_id': call['id'], 'name': call['name'], 'result': function_result(blocks)})
                        state['index'] += 1; await run_blocking(self.checkpoint, ident, state)
                        if (await run_blocking(self.store.get, ident))['status'] == 'completed':
                            native_stopped = await self.sandbox.stop(ident, expected_generation=generation)
                            await self.refresh_usage(ident, release=native_stopped is True, terminal_generation=generation)
                            return
                    inputs = state['results']
                run = await run_blocking(self.current, ident, generation)
                if run['spent'] >= run['cap']:
                    await run_blocking(self.store.update, ident, expected_generation=generation, status='budget_reached')
                    from .business_contracts import owned
                    if not owned(run):
                        await self.sandbox.stop(ident, expected_generation=generation)
                    return
                pin = await run_blocking(self.pin, ident)
                from .business_contracts import model_definitions
                body = {'model': pin['model'], 'input': inputs, 'system_instruction': SYSTEM,
                    'store': True, 'background': False, 'generation_config': {'max_output_tokens': pin['max_output_tokens']},
                    'tools': [{'type': 'function', 'name': d['name'], 'description': d['description'], 'parameters': d['input_schema']} for d in model_definitions(self.tools, run)]}
                if run['session_id']: body['previous_interaction_id'] = run['session_id']
                dispatch = uuid4().hex
                await run_blocking(self.store.event, ident, 'gemini_dispatch', {'id': dispatch, 'generation': generation})
                receipt = asyncio.create_task(self.dispatch_receipt(ident, dispatch, body, generation))
                try:
                    value = await asyncio.shield(receipt)
                except asyncio.CancelledError:
                    # Stop fences business writes first. Keep the bounded HTTP
                    # response alive so its ID can still be cancelled/accounted.
                    from .sandbox import settle_process_task
                    try:
                        await settle_process_task(receipt)
                    except Exception:
                        pass
                    raise
                interaction_id = value['id']
                await run_blocking(self.current, ident, generation)
                while value.get('status') in ('in_progress', 'queued'):
                    await asyncio.sleep(.5)
                    await run_blocking(self.current, ident, generation)
                    value = await self.request('GET', '/' + quote(interaction_id, safe=''))
                    if value.get('id') != interaction_id: raise Conflict('Gemini実行IDが一致しません')
                await run_blocking(self.current, ident, generation)
                await run_blocking(self.record_usage, ident, value)
                if value.get('status') not in ('requires_action', 'completed'): raise Conflict('Geminiの応答が完了しませんでした')
                calls = []
                for step in value.get('steps', []):
                    if step['type'] == 'function_call': calls.append(step)
                    elif step['type'] == 'model_output': await run_blocking(self.store.event, ident, 'agent.message', {'content': step.get('content', [])})
                if not calls:
                    await run_blocking(self.store.update, ident, expected_generation=generation, status='waiting_input')
                    await run_blocking(self.store.event, ident, 'business_model_idle', {'session_id': interaction_id, 'generation': generation})
                    await run_blocking(self.store.event, ident, 'notice', {'message': '応答が終了しました。業務完了証跡がないため完了扱いにしていません。'})
                    return
                if len({c['id'] for c in calls}) != len(calls): raise Conflict('Geminiのツール呼出しIDが重複しています')
                state = {'interaction': interaction_id, 'calls': calls, 'results': [], 'index': 0, 'pending': None}
                await run_blocking(self.checkpoint, ident, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = await run_blocking(self.store.get, ident)
            if current['generation'] != generation or current['status'] in ('completed', 'stopping', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending'):
                return
            await run_blocking(self.store.event, ident, 'error', {'message': '接続またはツール処理に失敗しました。再送せず結果照合が必要です。', 'class': type(exc).__name__})
            from .business_contracts import owned
            if owned(await run_blocking(self.store.get, ident)):
                await run_blocking(self.store.update, ident, expected_generation=generation, status='reconciliation_required')
                if getattr(self.tools, 'business', None):
                    await self.tools.business.kick_many([ident])
                return
            await self.stop(ident, expected_generation=generation)

    async def _stop(self, ident, caller=None, expected_generation=None, *, fenced=None):
        if (await run_blocking(self.store.get, ident))['status'] in ('stopped', 'rolled_back'): return
        if fenced is None:
            fenced = await run_blocking(self.store.fence, ident, expected_generation=expected_generation)
        if fenced is None:
            return
        previous, generation = fenced
        if (await run_blocking(self.store.get, ident))['generation'] != generation:
            return
        restored = previous['status'] == 'rollback_stop_pending'
        dispatches = {e['payload']['id'] for e in await run_blocking(self.store.events, ident) if e['kind'] == 'gemini_dispatch'}
        for task in (self.starting.get(ident), self.tasks.get(ident)):
            if (await run_blocking(self.store.get, ident))['generation'] != generation:
                return
            if task and not task.done() and task not in (asyncio.current_task(), caller):
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
        if (await run_blocking(self.store.get, ident))['generation'] != generation:
            return
        ids = list(dict.fromkeys(e['payload']['id'] for e in await run_blocking(self.store.events, ident)
                                if e['kind'] == 'gemini_response' and e['payload']['dispatch'] in dispatches))
        pending_status = 'rollback_stop_pending' if restored else 'stop_pending'
        native_stopped = await self.stop_native(ident, pending_status, generation)
        try:
            for interaction_id in ids:
                path = '/' + quote(interaction_id, safe='')
                value = await self.request('GET', path)
                if value.get('id') != interaction_id: raise Conflict('Gemini実行IDが一致しません')
                if value.get('status') not in QUIET:
                    await self.request('POST', path + '/cancel')
                    for _ in range(20):
                        value = await self.request('GET', path)
                        if value.get('id') != interaction_id: raise Conflict('Gemini実行IDが一致しません')
                        if value.get('status') in QUIET: break
                        await asyncio.sleep(.25)
                    else: raise Conflict('Geminiの停止確認待ちです')
            if await run_blocking(self.unsettled, ident): raise Conflict('Geminiの送信結果が未確定です')
            if ids: await self.refresh_usage(ident, release=native_stopped, terminal_generation=generation)
            elif native_stopped: await run_blocking(self.store.update, ident, expected_stop_generation=generation, reserved=0)
        except Exception:
            await run_blocking(self.store.update, ident, expected_stop_generation=generation, status=pending_status)
            local_status = '業務書込みは停止しました。' if native_stopped else '業務APIの停止は未確認です。'
            await run_blocking(self.store.event, ident, 'notice', {'message': local_status + 'Gemini側の停止・料金確認は未完了で、予約額を保持しています。'})
            return
        await run_blocking(self.store.update, ident, expected_stop_generation=generation, status=('rolled_back' if restored else 'stopped') if native_stopped else pending_status)
