"""Pin the application-owned prompt/tool contract without recording secrets."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .business_contracts import CONTRACTS, model_definitions
from .store import Conflict, now
from .catalog import OPERATIONS, READ_ONLY_WAITS
from .tasks import TASKS


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def binding(tools, run, system):
    task_id = run.get('task_id')
    contract = CONTRACTS.get(task_id)
    sources = {}
    for domain in run['domains']:
        config = tools.sandbox.configs.get(domain, {})
        manifest = config.get('source_manifest')
        if manifest:
            try:
                declared = json.loads(Path(manifest).read_text())
            except (OSError, ValueError) as exc:
                raise Conflict('業務APIの固定版を取得できません') from exc
            if not declared.get('source_revision') or not isinstance(declared.get('files'), dict):
                raise Conflict('業務APIの固定版が不完全です')
            sources[domain] = {'source_revision': declared['source_revision'],
                               'manifest_sha256': digest(declared)}
        else:
            sources[domain] = None
    provider_configuration = ({'model': os.environ.get('GEMINI_AGENT_MODEL')}
        if run['provider'] == 'gemini' else
        {'agent_sha256': digest(os.environ.get('CLAUDE_AGENT_ID')),
         'environment_sha256': digest(os.environ.get('CLAUDE_ENVIRONMENT_ID'))})
    return {'version': 1, 'provider': run['provider'], 'task_id': task_id,
            'native_sources': sources, 'provider_configuration': provider_configuration,
            'system_sha256': digest(system), 'task_sha256': digest(TASKS.get(task_id)),
            'contract_sha256': digest(asdict(contract) if contract else None),
            'tools_sha256': digest(model_definitions(tools, run))}


def record(store, tools, run, system, *, starting=False):
    previous = store.latest_event(run['id'], 'execution_binding')
    current = binding(tools, run, system)
    if previous:
        if previous['payload'] != current:
            raise Conflict('開始時と業務契約・接続先・ツール仕様が異なります。旧結果を照合せず再開しません')
        return current
    if not starting:
        # Legacy sessions have no historical prompt receipt. Do not fabricate
        # provenance by assigning today's contract to their previous work.
        return None
    store.event(run['id'], 'execution_binding', current, 'execution-binding-v1')
    saved = store.latest_event(run['id'], 'execution_binding')
    if saved is None or saved['payload'] != current:
        raise Conflict('開始時の業務契約を保存できませんでした')
    return current


def native_upgrade(store, tools, run, system, *, output_only=False):
    if output_only and (run['status'] != 'completed' or run.get('task_id') != 'hospital_pending_faxes' or run['domains'] != ['hospital']):
        return None
    if not output_only and run['status'] != 'reconciliation_required':
        return None
    previous = store.latest_event(run['id'], 'execution_binding')
    if not previous:
        return None
    before, after = previous['payload'], binding(tools, run, system)
    if before == after or {k: v for k, v in before.items() if k != 'native_sources'} != {k: v for k, v in after.items() if k != 'native_sources'}:
        return None
    if set(before['native_sources']) != set(after['native_sources']) or any(
            not before['native_sources'][name] or not after['native_sources'][name] for name in after['native_sources']):
        return None
    return {'previous_sha256': digest(before), 'current_sha256': digest(after),
            'previous_sources': before['native_sources'], 'current_sources': after['native_sources']}


def approve_native_upgrade(store, tools, run, system, *, generation, previous_sha256, current_sha256, output_only=False):
    plan = native_upgrade(store, tools, run, system, output_only=output_only)
    if (not plan or run['generation'] != generation or plan['previous_sha256'] != previous_sha256
            or plan['current_sha256'] != current_sha256):
        raise Conflict('表示後に依頼または業務API版が変更されています')
    current = binding(tools, run, system)
    if digest(current) != current_sha256:
        raise Conflict('引継ぎ対象の業務API版が変更されています')
    with store.tx() as db:
        saved = db.execute('SELECT generation,status FROM runs WHERE id=?', (run['id'],)).fetchone()
        prior = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='execution_binding' ORDER BY id DESC LIMIT 1", (run['id'],)).fetchone()
        if (saved['generation'] != generation or saved['status'] != run['status']
                or not prior or digest(json.loads(prior['payload'])) != previous_sha256):
            raise Conflict('版の引継ぎ中に依頼が変更されています')
        for row in db.execute("SELECT request FROM commands WHERE run_id=? AND status!='done'", (run['id'],)):
            name = json.loads(row['request'])['name']
            from .fax_preparation import enabled
            review_only = name == 'request_fax_approval' and enabled(run) and run['provider'] == 'gemini'
            if not review_only and name not in READ_ONLY_WAITS and not (OPERATIONS.get(name) and OPERATIONS[name].method == 'GET'):
                raise Conflict('結果未確定の更新があるため業務API版を引き継げません')
        # Retain the historical binding; the owner starts a new native-code
        # epoch under the identical prompt, tools and provider.
        for kind, payload in (('native_runtime_upgrade', {**plan, 'generation': generation, **({'output_only':True} if output_only else {})}), ('execution_binding', current)):
            db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                (run['id'], kind, json.dumps(payload, ensure_ascii=False), now()))
    return plan
