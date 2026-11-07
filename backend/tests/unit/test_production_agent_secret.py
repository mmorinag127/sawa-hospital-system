import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
from configure_agent_sidecar import configure


@pytest.mark.parametrize('existing', [False, True])
def test_approved_production_secret_without_worker_mutation(existing):
    instance = 'sawahospitalsystem:asia-northeast2:'
    names = ('DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'GOOGLE_OAUTH_CLIENT_ID', 'OCR_PIPELINE_BUCKET')
    entries = [{'name': n, 'value': '/cloudsql/' + instance + 'orders-prod' if n == 'DB_HOST' else 'test'} for n in names]
    if existing: entries.append({'name': 'GEMINI_API_KEY', 'valueFrom': {'secretKeyRef': {'name': 'existing', 'key': '1'}}})
    worker = {'metadata': {'name': 'worker-prod'}, 'spec': {'template': {'spec': {'containers': [{'env': entries}]}}}}
    before = copy.deepcopy(worker)
    web = {'metadata': {'name': 'web-prod', 'namespace': 'test'}, 'spec': {'template': {'metadata': {'annotations': {'run.googleapis.com/cloudsql-instances': instance + 'orders-prod'}}, 'spec': {'containers': [{'ports': [{'containerPort': 8080}]}]}}}}
    school = {'spec': {'template': {'metadata': {'annotations': {'run.googleapis.com/cloudsql-instances': instance + 'school-lunch-prod'}}, 'spec': {'containers': [{'env': [{'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {'name': 'school-lunch-prod-database-url', 'key': 'latest'}}}, {'name': 'PROCESS_GEMINI_MODEL', 'value': 'gemini-2.5-flash'}]}]}}}}
    config = {n: 'test' for n in ('operator_account', 'gemini_model', 'input_usd_per_million', 'output_usd_per_million', 'max_output_tokens')}
    image = 'registry/image@sha256:' + 'a' * 64
    result = configure(web, worker, config, image, image, '', school=school, environment='prod')
    env = result['spec']['template']['spec']['containers'][-1]['env']
    key = next(x for x in env if x['name'] == 'GEMINI_API_KEY')
    assert key['valueFrom']['secretKeyRef'] == {'name': 'existing' if existing else 'gemini-api-key', 'key': '1' if existing else 'latest'}
    assert worker == before
