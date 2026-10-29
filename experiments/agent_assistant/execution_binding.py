"""Pin the application-owned prompt/tool contract without recording secrets."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .business_contracts import CONTRACTS, model_definitions
from .store import Conflict
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
