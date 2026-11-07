"""Read-only production prerequisites, executed before any schema or service write."""

import json
import hashlib
import copy
import os
import re
import subprocess
from pathlib import Path

from hospital_release_context import PROJECT, REGION, require_context
from local_receipt_signing import verify_signed


def cloud(*args):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--format=json'],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError('production prerequisite unavailable: ' + ' '.join(args[:3]))
    return json.loads(result.stdout) if result.stdout.strip() else None


def validate_agent_config(raw):
    config = json.loads(raw)
    fields = ('operator_account', 'gemini_model', 'input_usd_per_million',
              'output_usd_per_million', 'max_output_tokens')
    if not isinstance(config, dict) or any(not isinstance(config.get(key), str) or not config[key].strip() for key in fields):
        raise RuntimeError('explicit production agent settings required')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', config['operator_account']):
        raise RuntimeError('production agent operator account invalid')
    if not config['max_output_tokens'].isdigit() or int(config['max_output_tokens']) <= 0:
        raise RuntimeError('production agent token limit invalid')
    from decimal import Decimal
    for key in ('input_usd_per_million', 'output_usd_per_million'):
        amount = Decimal(config[key])
        if not amount.is_finite() or amount < 0:
            raise RuntimeError('production agent accounting configuration invalid')
    return config


def migration_source_digest(root):
    digest = hashlib.sha256()
    paths = [*root.glob("backend/alembic/**/*.py"), *root.glob("backend/migrations/**/*.py"),
             root / "backend/alembic/catalog_contracts.json", root / "backend/alembic.ini",
             *root.glob("backend/src/maintenance/**/*.py"),
             root / "scripts/run_stg_schema_adoption.py"]
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def revision_images(revision):
    containers = revision.get("spec", {}).get("containers", [])
    status = revision.get("status", {})
    resolved = {row.get("name"): row.get("imageDigest") for row in status.get("containerStatuses", [])}
    if len(containers) == 1 and status.get("imageDigest"):
        return [status["imageDigest"]]
    return [resolved.get(row.get("name")) or row.get("image", "") for row in containers]


def require_rollback_compatibility(service, target):
    root = Path(__file__).resolve().parents[1]
    evidence = verify_signed(json.loads((root /
                           "docs/release-evidence/rollback-db-compatibility.json").read_text()))
    if evidence.get("migration_source_sha256") != migration_source_digest(root):
        raise RuntimeError("rollback evidence does not match migration source")
    entry = evidence["services"][service]
    if (entry["tested_revision_uid"] != target["uid"] or entry["images"] != target["images"]
            or entry["passed"] is not True or entry.get("runtime_executed") is not True
            or not re.fullmatch(r"[a-f0-9]{64}", entry.get("snapshot_sha256", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", entry.get("result_sha256", ""))):
        raise RuntimeError("missing matching rollback database compatibility evidence: " + service)
    result = root / "docs/release-evidence" / (service + "-rollback-result.json")
    data = result.read_bytes()
    execution = json.loads(data)
    if (hashlib.sha256(data).hexdigest() != entry["result_sha256"]
            or execution.get("exit_code") != 0 or execution.get("failed") != 0
            or execution.get("skipped") != 0 or not isinstance(execution.get("passed"), int)
            or execution["passed"] <= 0 or execution.get("images") != target["images"]
            or execution.get("snapshot_sha256") != entry["snapshot_sha256"]
            or execution.get("migration_source_sha256") != evidence["migration_source_sha256"]):
        raise RuntimeError("rollback runtime result does not match signed evidence: " + service)


def require_database_restore_rehearsal():
    root = Path(__file__).resolve().parents[1]
    proof = verify_signed(json.loads((root / "docs/release-evidence/database-restore-rehearsal.json").read_text()))
    if (proof.get("migration_source_sha256") != migration_source_digest(root)
            or proof.get("instance") != PROJECT + ":" + REGION + ":orders-prod"
            or proof.get("database") != "orders"
            or proof.get("production_modified") is not False
            or not re.fullmatch(r"[a-f0-9]{64}", proof.get("dump_sha256", ""))
            or any(proof.get(key) is not True for key in (
                "formal_head_verified", "existing_values_preserved", "restored_sequences_preserved"))
            or any(proof.get(key, {}).get("verified") is not True
                   for key in ("post_migration_backup", "pre_migration_restore"))):
        raise RuntimeError("matching signed local database restore rehearsal required")


def run(capture_only=False):
    if require_context(os.environ) != 'prod':
        raise RuntimeError('production release required')
    strategy = os.environ.get("ROLLBACK_STRATEGY", "migrated_database")
    if strategy not in {"migrated_database", "restore_database"}:
        raise RuntimeError("explicit supported rollback strategy required")
    if strategy == "restore_database":
        require_database_restore_rehearsal()
    agent_config = validate_agent_config(os.environ.get('PROD_AGENT_RUNTIME_CONFIG', ''))
    for prefix in ('AUTOMATION_AUTH_', 'SCHOOL_LUNCH_AUTOMATION_AUTH_'):
        for suffix in ('AUDIENCE', 'EMAIL', 'SUBJECT'):
            value = os.environ.get(prefix + suffix, '')
            if not value or value != value.strip() or any(char in value for char in ',\r\n\0'):
                raise RuntimeError('explicit production automation setting required: ' + prefix + suffix)
    master = os.environ.get('FACILITY_MASTER_URI', '')
    if not master.startswith('gs://'+PROJECT+'-prod-templates/'):
        raise RuntimeError('explicit production canonical facility URI required')
    expected = {'web-prod': 'orders-prod', 'worker-prod': 'orders-prod',
                'ocr-pipeline-prod': 'orders-prod', 'price-api-prod': 'school-lunch-prod',
                'shift-api-prod': 'shift-prod'}
    maintenance_path = Path(os.environ['RUNNER_TEMP']) / 'hospital-maintenance.json'
    maintenance = None
    if maintenance_path.exists():
        if capture_only:
            raise RuntimeError('never recapture an existing maintenance operation')
        from production_maintenance import load, assert_stopped, verify_display
        _, maintenance = load()
        assert_stopped()
        verify_display()
    if capture_only:
        expected = {name: db for name, db in expected.items() if db == 'orders-prod'}
    descriptors = {}
    rollback = {}
    for name, database in expected.items():
        descriptor = cloud('run','services','describe',name,'--region='+REGION)
        if descriptor.get('metadata', {}).get('labels', {}).get('sawa-maintenance-run') and not maintenance:
            raise RuntimeError('unfinished maintenance requires original-run recovery: ' + name)
        descriptors[name] = descriptor
        template = descriptor['spec']['template']
        instances = template.get('metadata',{}).get('annotations',{}).get('run.googleapis.com/cloudsql-instances','').split(',')
        if f'{PROJECT}:{REGION}:{database}' not in instances or any(item.endswith('-stg') for item in instances):
            raise RuntimeError('production database attachment mismatch: '+name)
        ready = descriptor.get('status',{}).get('latestReadyRevisionName')
        if not ready:
            raise RuntimeError('ready production dependency required: '+name)
        if name in ('worker-prod','web-prod','ocr-pipeline-prod'):
            if maintenance:
                original = maintenance['services'][name]
                old = cloud('run', 'revisions', 'describe', original['revision'], '--region=' + REGION)
                if old['metadata']['uid'] != original['uid'] or revision_images(old) != original['images']:
                    raise RuntimeError('original rollback identity changed: ' + name)
                if name != 'web-prod':
                    from production_maintenance import serving
                    if serving(descriptor) != original['revision']:
                        raise RuntimeError('writer changed before migration: ' + name)
                rollback[name] = original
                continue
            traffic = [item for item in descriptor.get('status', {}).get('traffic', []) if item.get('percent', 0)]
            if len(traffic) != 1 or traffic[0].get('percent') != 100 or not traffic[0].get('revisionName'):
                raise RuntimeError('single serving rollback revision required: ' + name)
            revision = cloud('run','revisions','describe',traffic[0]['revisionName'],'--region='+REGION)
            images = revision_images(revision)
            if not images:
                raise RuntimeError('rollback image unresolved: '+name)
            runtime_digest = revision.get('status', {}).get('imageDigest', '')
            if len(images) == 1 and re.fullmatch(r'.+@sha256:[0-9a-f]{64}', runtime_digest):
                images = [runtime_digest]
            for image in images:
                if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', image):
                    raise RuntimeError('immutable serving rollback image required: ' + name)
            metadata = revision["metadata"]
            if (metadata["labels"].get("serving.knative.dev/service") != name
                    or not metadata.get("uid") or metadata.get("deletionTimestamp")
                    or not any(c["type"] == "Ready" and c["status"] == "True"
                               for c in revision["status"]["conditions"])):
                raise RuntimeError('retained rollback revision not ready: ' + name)
            scaling = descriptor.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic")
            if strategy == "restore_database" and scaling != "automatic":
                raise RuntimeError("database restoration requires recorded automatic service scaling")
            rollback[name] = {"revision": traffic[0]["revisionName"], "uid": metadata["uid"],
                              "images": images, "scaling": "auto" if scaling == "automatic" else scaling}
            if strategy == "migrated_database":
                require_rollback_compatibility(name, rollback[name])
    # Reuse the exact deployment validation without writing a service or token.
    from configure_agent_sidecar import configure
    if not capture_only:
        configure(copy.deepcopy(descriptors['web-prod']), descriptors['worker-prod'],
                  agent_config, os.environ['PRODUCTION_FRONTEND_IMAGE'],
                  os.environ['PRODUCTION_AGENT_IMAGE'], '', school=descriptors['price-api-prod'],
                  portal_origin=os.environ['PORTAL_PUBLIC_ORIGIN'], environment='prod')
    from stg_ocr_producer_deploy import ready_revision, replacement
    ready_revision(descriptors['ocr-pipeline-prod'])
    replacement(descriptors['ocr-pipeline-prod'], os.environ['PRODUCTION_PRODUCER_IMAGE'], 'prod')
    for name in (() if capture_only else ('shift-web-prod','price-web-prod')):
        data = cloud('run','services','describe',name,'--region='+REGION)
        if not data.get('status',{}).get('latestReadyRevisionName'):
            raise RuntimeError('ready production frontend required: '+name)
    # Production mode validates existing IAM/queues/schedulers only; it never provisions them.
    from hospital_delivery_config import bootstrap
    bootstrap(allow_paused=True)
    receipt = {"run": os.environ["GITHUB_RUN_ID"], "sha": os.environ["GITHUB_SHA"],
               "ref": os.environ["GITHUB_REF"], "project": PROJECT, "region": REGION,
               "services": rollback, "rollback_strategy": strategy}
    (Path(os.environ["RUNNER_TEMP"]) / "hospital-rollback.json").write_text(json.dumps(receipt))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write("\n### Pre-migration rollback targets\n\n")
        summary.write("Retained ready revisions serving 100% of traffic. "
                      "Database compatibility must be verified separately.\n\n")
        for service, record in sorted(rollback.items()):
            summary.write(f"- `{service}`: `{record['revision']}` "
                          + ", ".join(f"`{image}`" for image in record["images"]) + "\n")


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--capture-only', action='store_true')
        run(capture_only=parser.parse_args().capture_only)
    except Exception:
        raise SystemExit('production preflight blocked; cloud/credential details suppressed')
    print('production prerequisites verified before schema changes')
