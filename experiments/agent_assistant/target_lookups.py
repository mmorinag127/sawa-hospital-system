"""Read-only target discovery outlives a single HTTP request."""
import asyncio
import json

from .store import Conflict


class TargetLookups:
    def __init__(self, store, tools):
        self.store, self.tools = store, tools
        self.jobs = {}

    def create(self, ident, owner, task_id, inputs, authorization):
        with self.store.tx() as db:
            row = db.execute('SELECT * FROM target_lookups WHERE id=?', (ident,)).fetchone()
            if row:
                if (row['owner'], row['task_id'], json.loads(row['inputs'])) != (owner, task_id, inputs):
                    raise Conflict('取得依頼の内容が一致しません')
            else:
                db.execute("INSERT INTO target_lookups VALUES(?,?,?,?,'queued',NULL)", (ident, owner, task_id, json.dumps(inputs, sort_keys=True)))
        return self.get(ident, owner, authorization)

    def get(self, ident, owner, authorization):
        row = self.store.target_lookup(ident, owner)
        return self.result(row, authorization)

    def result(self, row, authorization):
        ident = row['id']
        if row['state'] in ('queued', 'running') and (ident not in self.jobs or self.jobs[ident].done()):
            self.jobs[ident] = asyncio.create_task(self.collect(row, authorization))
        return {'lookup_id': ident, 'state': row['state'], 'result': json.loads(row['payload']) if row['payload'] else None}

    async def collect(self, row, authorization):
        try:
            result = await self.tools.flow.options(row['task_id'], json.loads(row['inputs']), row['owner'], authorization)
            state = 'completed'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {'message': str(exc) if isinstance(exc, (ValueError, Conflict)) else '登録内容の取得をシステム側で確認しています。'}
            state = 'failed'
        with self.store.tx() as db:
            db.execute('UPDATE target_lookups SET state=?,payload=? WHERE id=?', (state, json.dumps(result, ensure_ascii=False), row['id']))

    async def close(self):
        for job in self.jobs.values():
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
