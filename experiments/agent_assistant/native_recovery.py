"""Recover an owned native response, never infer a missing write from a timeout."""
import hashlib
import json

import httpx

from .catalog import OPERATIONS
from .store import Conflict
from .sandbox import run_blocking


def receipt_generation(store, run, receipt):
    generation = receipt['generation']
    for event in store.events(run['id']):
        if event['kind'] == 'gateway_recovery_fence' and event['payload']['previous_generation'] == generation:
            generation = event['payload']['generation']
    return generation == run['generation']


async def reconcile(engine, run):
    tools, store = engine.tools, engine.store
    def read_pending():
        with store.read() as db:
            pending = [dict(row) for row in db.execute("SELECT * FROM commands WHERE run_id=? AND status NOT IN ('done','recoverable_wait','recoverable_read') ORDER BY rowid", (run['id'],))]
        receipts = [event['payload'] for event in store.events(run['id']) if event['kind'] == 'native_response_receipt']
        return pending, receipts
    pending, receipts = await run_blocking(read_pending)
    for row in pending:
        request = json.loads(row['request'])
        operation = OPERATIONS.get(request['name'])
        if operation and operation.method == 'GET':
            def recover_read():
                with store.tx() as db:
                    db.execute("UPDATE commands SET status='recoverable_read' WHERE run_id=? AND id=?", (run['id'], row['id']))
                store.event(run['id'], 'business_read_recovery', {'command_id': row['id']})
            await run_blocking(recover_read)
            continue
        if request['name'] in ('wait_for_native_job', 'wait_for_task_decision', 'wait_for_fax_work'):
            continue
        def matching_receipts():
            return [receipt for receipt in receipts if receipt['command_id'] == row['id'] and receipt_generation(store, run, receipt)
                    and receipt['name'] == request['name'] and receipt['request_sha256'] == hashlib.sha256(row['request'].encode()).hexdigest()]
        matching = await run_blocking(matching_receipts)
        if not operation or operation.method == 'GET' or len(matching) != 1 or not 200 <= matching[0]['status'] < 300:
            raise Conflict('保存応答が未確定のため、更新は繰り返さず管理者が実行結果を確認します')
        from .tools import Arguments
        receipt = matching[0]
        args = Arguments.model_validate(receipt['parameters'])
        await tools.flow.scoped(run, request['name'], args)
        result = await tools.native_result(run, request['name'], args, httpx.Response(receipt['status'], json=receipt['data']))
        if request['name'] == 'lunch_save_price_source' and run.get('task_id') == 'lunch_daily_bundle':
            draft_id = result.get('data', {}).get('draft_id')
            if not draft_id:
                raise Conflict('保存した実施価格の応答が不完全です')
            events = await run_blocking(store.events, run['id'])
            bindings = [event['payload'] for event in events if event['kind'] == 'daily_price_binding']
            if not bindings or bindings[-1] != {'command_id': row['id'], 'draft_id': draft_id}:
                await run_blocking(store.event, run['id'], 'daily_price_binding', {'command_id': row['id'], 'draft_id': draft_id})
        result['command_id'] = row['id']
        def finish_recovery():
            store.finish_command(run['id'], row['id'], result)
            store.event(run['id'], 'tool_result', {'tool': request['name'], 'result': result})
            store.event(run['id'], 'business_write_recovery', {'command_id': row['id'], 'source': 'persisted_native_response'})
        await run_blocking(finish_recovery)
