"""Actions-only planned maintenance; never restore a database or reopen on failure."""

import json
import copy
import os
from pathlib import Path
import time
import subprocess
import tempfile
import re
from urllib.error import HTTPError
from urllib.request import urlopen

from hospital_production_preflight import cloud, revision_images
from hospital_release_context import PROJECT, REGION, require_context

WRITERS = ("worker-prod", "ocr-pipeline-prod")
SERVICES = ("web-prod", *WRITERS)
QUEUES = {"hospital-ingest-prod", "hospital-workflow-ocr-prod"}
SCHEDULES = {"hospital-ingest-drain-prod", "hospital-workflow-ocr-drain-prod",
             "ingest-retry-prod", "shipping-tracking-refresh-prod"}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def context():
    require(require_context(os.environ) == "prod", "production Actions required")
    require([item.get("account") for item in cloud("auth", "list", "--filter=status:ACTIVE")] ==
            [f"sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com"],
            "production deploy identity required")
    return Path(os.environ["RUNNER_TEMP"]) / "hospital-maintenance.json"


def serving(service, allowed_tag=None):
    traffic = service.get("status", {}).get("traffic", [])
    allowed = {allowed_tag} if isinstance(allowed_tag, str) else set(allowed_tag or ())
    require(not any(item.get("tag") and item["tag"] not in allowed for item in traffic),
            "tagged routes require explicit review")
    active = [item for item in traffic if item.get("percent")]
    require(len(active) == 1 and active[0].get("percent") == 100,
            "one serving revision required")
    return active[0]["revisionName"]


def save(path, record):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def mutation(path, record, operation, *args):
    entry = {"operation": operation, "state": "started"}
    record.setdefault("operations", []).append(entry)
    save(path, record)
    result = cloud(*args)
    entry["state"] = "returned"
    save(path, record)
    return result


def download_record(phase, *, allow_pristine=False):
    require(require_context(os.environ) == "prod", "production context required")
    require(phase in {"start", "database"}, "unknown maintenance phase")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"]
    prefix = "hospital-maintenance-" + ("start-" if phase == "start" else "") + run + "-"
    output = subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/actions/runs/{run}/artifacts?per_page=100"], text=True)
    artifacts = [item for page in json.loads(output) for item in page["artifacts"]
                 if item["name"].startswith(prefix) and item["name"][len(prefix):].isdigit()
                 and not item.get("expired")]
    if not artifacts and allow_pristine:
        return False
    require(artifacts, "same-run maintenance artifact missing")
    selected = max(artifacts, key=lambda item: int(item["name"][len(prefix):]))
    require(int(selected["name"][len(prefix):]) <= int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "future attempt artifact rejected")
    destination = Path(os.environ["RUNNER_TEMP"])
    with tempfile.TemporaryDirectory(dir=destination) as directory:
        subprocess.run(["gh", "run", "download", run, "--repo", repository,
                        "--name", selected["name"], "--dir", directory], check=True,
                       capture_output=True)
        maintenance_file = Path(directory) / 'hospital-maintenance.json'
        if not maintenance_file.exists() and allow_pristine:
            rollback = json.loads((Path(directory) / 'hospital-rollback.json').read_text())
            require(rollback.get('run') == run and rollback.get('sha') == os.environ['GITHUB_SHA']
                    and rollback.get('ref') == os.environ['GITHUB_REF']
                    and set(rollback.get('services', {})) == set(SERVICES), 'partial start artifact mismatch')
            for name, original in rollback['services'].items():
                service = cloud('run', 'services', 'describe', name, '--region=' + REGION)
                tags = [item['tag'] for item in service.get('status', {}).get('traffic', []) if item.get('tag')]
                require(not service['metadata'].get('labels', {}).get('sawa-maintenance-run')
                        and service['metadata'].get('annotations', {}).get('run.googleapis.com/scalingMode', 'automatic') == 'automatic'
                        and serving(service, tags) == original['revision'], 'partial start already changed services')
                revision = cloud('run', 'revisions', 'describe', original['revision'], '--region=' + REGION)
                require(revision['metadata']['uid'] == original['uid']
                        and revision_images(revision) == original['images'], 'partial original identity changed')
            return False
        record = json.loads(maintenance_file.read_text())
        require(record.get("run") == run and record.get("sha") == os.environ["GITHUB_SHA"]
                and record.get("ref") == os.environ["GITHUB_REF"], "artifact source mismatch")
        if phase == "start":
            rollback = json.loads((Path(directory) / "hospital-rollback.json").read_text())
            require(rollback.get("run") == run and rollback.get("sha") == record["sha"]
                    and rollback.get("services") == record["services"], "original rollback mismatch")
            save(destination / "hospital-rollback.json", rollback)
        save(destination / "hospital-maintenance.json", record)
    return True


def begin(image, original_run):
    context()
    repository, run = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_RUN_ID']
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repository}/actions/runs/{run}/jobs?filter=all&per_page=100'], text=True))
    downstream = {'prod-db-bootstrap-gate', 'deploy-backend', 'deploy-frontend'}
    require(not any(item['name'] in downstream and
                    (item.get('status') == 'in_progress' or
                     (item.get('status') == 'completed' and item.get('conclusion') != 'skipped'))
                    for page in pages for item in page['jobs']), 'cannot restart maintenance after downstream work')
    found = download_record('start', allow_pristine=True)
    if not found and original_run:
        resume_start(original_run)
        found = True
    if not found:
        from hospital_production_preflight import run as preflight
        preflight(capture_only=True)
        capture()
        show(image)
    else:
        _, record = load()
        require(not any(record.get(key) for key in ('database_drained', 'portal_bootstrap_complete',
                    'candidate_revision', 'writers_verified_open', 'reopened')), 'maintenance start already advanced')
        from hospital_production_preflight import require_runtime_act_as
        descriptors = {name: cloud('run', 'services', 'describe', name, '--region=' + REGION)
                       for name in SERVICES}
        require_runtime_act_as(descriptors)
        for name in SERVICES:
            original = record['services'][name]
            revision = cloud('run', 'revisions', 'describe', original['revision'], '--region=' + REGION)
            require(revision['metadata']['uid'] == original['uid']
                    and revision_images(revision) == original['images'], 'original identity changed on retry')
            verify_captured_tags(record, name, descriptors[name], allow_retired=True)
        portal = serving(descriptors['web-prod'], [item['tag'] for item in
            record.get('tagged_routes', {}).get('web-prod', [])])
        if portal == record['services']['web-prod']['revision']:
            show(record.get('maintenance_image') or image)
        else:
            require(portal == record.get('maintenance_revision'), 'portal changed during start retry')
            verify_display()
    stop_writers()


def resume_start(original_run):
    path = context()
    require(re.fullmatch(r'[1-9][0-9]*', original_run or ''), 'explicit original run required')
    require(original_run != os.environ['GITHUB_RUN_ID'], 'use same-run artifact for retry')
    repository = os.environ['GITHUB_REPOSITORY']
    owner = repository.split('/')[0]
    run = json.loads(subprocess.check_output(
        ['gh', 'api', f'repos/{repository}/actions/runs/{original_run}'], text=True))
    require(run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'failure'
            and run.get('path') == '.github/workflows/deploy-prod.yml'
            and run.get('actor', {}).get('login') == owner
            and run.get('triggering_actor', {}).get('login') == owner
            and str(run.get('head_branch', '')).startswith('release/prod-'),
            'only an owner-dispatched failed production start can resume')
    original_sha = run.get('head_sha', '')
    require(re.fullmatch(r'[a-f0-9]{40}', original_sha), 'invalid original source')
    subprocess.run(['git', 'merge-base', '--is-ancestor', original_sha, os.environ['GITHUB_SHA']],
                   check=True, capture_output=True)
    unchanged = ('backend/src', 'backend/scripts', 'backend/alembic', 'backend/migrations', 'backend/alembic.ini',
                 'frontend/src', 'ocr_pipeline/app', 'agent_runtime', 'experiments',
                 'scripts/run_stg_schema_adoption.py', 'scripts/run_prod_schema_adoption.py')
    require(not subprocess.check_output(['git', 'diff', '--name-only', original_sha,
                os.environ['GITHUB_SHA'], '--', *unchanged], text=True).strip(),
            'business runtime or migration changed since interrupted start')
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repository}/actions/runs/{original_run}/jobs?filter=latest&per_page=100'], text=True))
    jobs = {item['name']: item for page in pages for item in page['jobs']}
    require(jobs.get('production-approval', {}).get('conclusion') == 'failure'
            and all(jobs.get(name, {}).get('conclusion') == 'skipped'
                    for name in ('prod-db-bootstrap-gate', 'deploy-backend', 'deploy-frontend')),
            'original run advanced beyond maintenance start')
    all_pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repository}/actions/runs/{original_run}/jobs?filter=all&per_page=100'], text=True))
    require(not any(item['name'] in {'prod-db-bootstrap-gate', 'deploy-backend', 'deploy-frontend'}
                    and item.get('conclusion') != 'skipped' for page in all_pages for item in page['jobs']),
            'original run executed downstream work in an earlier attempt')
    artifact = f'hospital-maintenance-start-{original_run}-{run["run_attempt"]}'
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        subprocess.run(['gh', 'run', 'download', original_run, '--repo', repository,
                        '--name', artifact, '--dir', directory], check=True, capture_output=True)
        original = json.loads((Path(directory) / 'hospital-maintenance.json').read_text())
        rollback = json.loads((Path(directory) / 'hospital-rollback.json').read_text())
    require(original.get('run') == original_run and original.get('sha') == original_sha
            and original.get('ref') == 'refs/heads/' + run['head_branch']
            and rollback.get('run') == original_run and rollback.get('sha') == original_sha
            and rollback.get('rollback_strategy') == os.environ.get('ROLLBACK_STRATEGY') == 'restore_database'
            and rollback.get('services') == original.get('services')
            and set(original.get('services', {})) == set(SERVICES), 'original artifact mismatch')
    require(original.get('maintenance_visible') is True
            and original.get('database_drained') is False
            and not any(original.get(key) for key in ('portal_bootstrap_complete', 'candidate_revision',
                        'candidate_checks_passed', 'writers_verified_open', 'reopened')),
            'only pre-migration maintenance start can resume')
    initial = original
    seen = set()
    while initial.get('resume_origin'):
        require(initial['run'] not in seen, 'cyclic maintenance provenance')
        seen.add(initial['run'])
        origin = initial['resume_origin']
        initial = origin['maintenance']
        require(initial['run'] == origin['run'] and initial['sha'] == origin['sha']
                and initial['services'] == original['services']
                and initial['maintenance_revision'] == original['maintenance_revision'],
                'maintenance provenance changed')
    build_run = initial['run']
    require(re.fullmatch(r'[1-9][0-9]*', build_run), 'invalid maintenance build run')
    build_info = json.loads(subprocess.check_output(
        ['gh', 'api', f'repos/{repository}/actions/runs/{build_run}'], text=True))
    require(build_info.get('head_sha') == initial['sha'] and build_info.get('event') == 'workflow_dispatch'
            and build_info.get('path') == '.github/workflows/deploy-prod.yml'
            and build_info.get('actor', {}).get('login') == owner
            and build_info.get('triggering_actor', {}).get('login') == owner,
            'original maintenance build provenance mismatch')
    build_pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repository}/actions/runs/{build_run}/jobs?filter=latest&per_page=100'], text=True))
    builds = [item for page in build_pages for item in page['jobs']
              if item['name'] == 'build-frontend' and item.get('conclusion') == 'success']
    require(len(builds) == 1, 'original maintenance build must have succeeded')
    build_log = subprocess.check_output(['gh', 'run', 'view', build_run, '--repo', repository,
        '--job', str(builds[0]['id']), '--log'], text=True)
    digests = set(re.findall(r' prod-frontend-' + initial['sha'][:12]
        + r'-[0-9]{8}-[0-9]{6}-maintenance: digest: (sha256:[0-9a-f]{64}) size: [0-9]+\s*$',
        build_log, re.MULTILINE))
    require(len(digests) == 1, 'unique original maintenance push digest required')
    maintenance_image = f'{REGION}-docker.pkg.dev/{PROJECT}/backend/frontend@' + next(iter(digests))
    descriptors = {name: cloud('run', 'services', 'describe', name, '--region=' + REGION)
                   for name in SERVICES}
    from restore_hospital_database import assert_database_inventory
    assert_database_inventory()
    inventory = cloud('run', 'services', 'list', '--region=' + REGION)
    attached = {item['metadata']['name'] for item in inventory
                if f'{PROJECT}:{REGION}:orders-prod' in item.get('spec', {}).get('template', {})
                .get('metadata', {}).get('annotations', {}).get('run.googleapis.com/cloudsql-instances', '').split(',')}
    require(attached == set(SERVICES), 'unreviewed database consumer service')
    from hospital_production_preflight import require_runtime_act_as
    require_runtime_act_as(descriptors)
    marker_run = original.get('maintenance_owner_run', original_run)
    for name, service in descriptors.items():
        prior = original['services'][name]
        detail = cloud('run', 'revisions', 'describe', prior['revision'], '--region=' + REGION)
        require(detail['metadata']['uid'] == prior['uid'] and revision_images(detail) == prior['images'],
                'original revision identity changed')
        routes = verify_captured_tags(original, name, service, allow_retired=True)
        if name == 'web-prod':
            require(serving(service) == original['maintenance_revision']
                    and service['metadata'].get('labels', {}).get('sawa-maintenance-run') == marker_run,
                    'original maintenance portal changed')
            revision = cloud('run', 'revisions', 'describe', original['maintenance_revision'], '--region=' + REGION)
            require(revision_images(revision) == [maintenance_image]
                    and (not original.get('maintenance_uid')
                         or revision['metadata']['uid'] == original['maintenance_uid'])
                    and revision['spec']['containers'][0].get('command') == ['node']
                    and revision['spec']['containers'][0].get('args') == ['/sawa-maintenance/server.cjs']
                    and len(revision['spec']['containers']) == 1, 'maintenance runtime changed')
        else:
            require(serving(service, [item['tag'] for item in routes]) == prior['revision'],
                    'writer changed before maintenance continuation')
    origin = {'run': original_run, 'sha': original_sha, 'artifact': artifact,
              'maintenance': original, 'rollback': rollback}
    record = copy.deepcopy(original)
    record.update(run=os.environ['GITHUB_RUN_ID'], sha=os.environ['GITHUB_SHA'],
                  ref=os.environ['GITHUB_REF'], resume_origin=origin, operations=[],
                  maintenance_image=maintenance_image, maintenance_owner_run=marker_run)
    current_rollback = copy.deepcopy(rollback)
    current_rollback.update(run=record['run'], sha=record['sha'], ref=record['ref'], resume_origin=origin)
    save(path.parent / 'hospital-rollback.json', current_rollback)
    save(path, record)
    verify_display()


def capture():
    path = context()
    require(not path.exists(), "never replace an existing maintenance record")
    inventory = cloud("run", "services", "list", "--region=" + REGION)
    attached = {item["metadata"]["name"] for item in inventory
                if f"{PROJECT}:{REGION}:orders-prod" in item.get("spec", {}).get("template", {})
                .get("metadata", {}).get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "").split(",")}
    require(attached == set(SERVICES), "unreviewed database consumer service")
    from restore_hospital_database import assert_database_inventory
    assert_database_inventory()
    record = {"run": os.environ["GITHUB_RUN_ID"], "sha": os.environ["GITHUB_SHA"],
              "ref": os.environ["GITHUB_REF"], "services": {}, "tagged_routes": {}, "queues": {},
              "schedules": {}, "writers_stopped": False, "database_drained": False,
              "application_checks_passed": False, "reopened": False}
    for name in SERVICES:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        require(not service["metadata"].get("labels", {}).get("sawa-maintenance-run"),
                "existing maintenance requires original-run recovery; never recapture it")
        if name == "web-prod":
            containers = service["spec"]["template"]["spec"]["containers"]
            require(containers and containers[0].get("ports")
                    and not any(item.get("ports") for item in containers[1:]),
                    "portal ingress container must be unambiguous")
        tagged = [item for item in service.get("status", {}).get("traffic", []) if item.get("tag")]
        routes = []
        for item in tagged:
            detail = cloud("run", "revisions", "describe", item["revisionName"], "--region=" + REGION)
            require(detail["metadata"].get("labels", {}).get("serving.knative.dev/service") == name,
                    "tagged revision does not belong to service")
            routes.append({"tag": item["tag"], "revision": item["revisionName"],
                           "uid": detail["metadata"]["uid"], "images": revision_images(detail)})
        record["tagged_routes"][name] = routes
        revision = serving(service, [item["tag"] for item in routes])
        mode = service["metadata"].get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic")
        require(mode == "automatic", "unexpected service scaling before maintenance")
        detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
        require(any(item.get("type") == "Ready" and item.get("status") == "True"
                    for item in detail.get("status", {}).get("conditions", [])), "original revision not ready")
        record["services"][name] = {"revision": revision, "uid": detail["metadata"]["uid"],
                                    "images": revision_images(detail), "scaling": "auto"}
    rollback = json.loads((path.parent / "hospital-rollback.json").read_text())
    require(rollback.get("run") == record["run"] and rollback.get("sha") == record["sha"]
            and rollback.get("services") == record["services"],
            "preflight rollback targets changed before maintenance")
    for kind, names, field in (("tasks", QUEUES, "queues"), ("scheduler", SCHEDULES, "schedules")):
        resource = "queues" if kind == "tasks" else "jobs"
        for item in cloud(kind, resource, "list", "--location=" + REGION):
            name = item["name"].split("/")[-1]
            if name in names:
                require(item.get("state") in {"RUNNING", "ENABLED", "PAUSED"}, "unexpected transport state")
                record[field][name] = item["state"]
    save(path, record)
    return record


def load():
    path = context()
    record = json.loads(path.read_text())
    require(all(record.get(key) == os.environ[env] for key, env in
                (("run", "GITHUB_RUN_ID"), ("sha", "GITHUB_SHA"), ("ref", "GITHUB_REF"))),
            "maintenance record belongs to another run")
    require(set(record["services"]) == set(SERVICES), "maintenance service inventory mismatch")
    return path, record


def verify_captured_tags(record, name, service, *, allow_retired=False):
    routes = record.get("tagged_routes", {}).get(name, [])
    observed = {(item["tag"], item["revisionName"]) for item in service.get("status", {}).get("traffic", [])
                if item.get("tag")}
    expected = {(item["tag"], item["revision"]) for item in routes}
    require(observed <= expected if allow_retired else observed == expected, "captured tags changed")
    for item in routes:
        detail = cloud("run", "revisions", "describe", item["revision"], "--region=" + REGION)
        require(detail["metadata"]["uid"] == item["uid"] and revision_images(detail) == item["images"],
                "captured tagged revision identity changed")
    return [item for item in routes if (item["tag"], item["revision"]) in observed]


def stop_writers():
    path, record = load()
    verify_display()
    from hospital_production_preflight import require_runtime_act_as
    require_runtime_act_as({name: cloud('run', 'services', 'describe', name, '--region=' + REGION)
                            for name in SERVICES})
    record['maintenance_visible'] = True
    save(path, record)
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        routes = verify_captured_tags(record, name, service, allow_retired=True)
        require(serving(service, [item["tag"] for item in routes]) == record["services"][name]["revision"],
                "writer revision changed")
        if routes:
            mutation(path, record, "retire-tags:" + name, "run", "services", "update-traffic", name,
                     "--remove-tags=" + ",".join(item["tag"] for item in routes), "--region=" + REGION, "--quiet")
        require(serving(cloud("run", "services", "describe", name, "--region=" + REGION))
                == record["services"][name]["revision"], "writer traffic changed while retiring tags")
    for field, kind, resource in (("queues", "tasks", "queues"), ("schedules", "scheduler", "jobs")):
        for name in record[field]:
            current = cloud(kind, resource, "describe", name, "--location=" + REGION)
            if current.get("state") != "PAUSED":
                mutation(path, record, "pause:" + name, kind, resource, "pause", name, "--location=" + REGION)
            require(cloud(kind, resource, "describe", name, "--location=" + REGION).get("state") == "PAUSED",
                    "transport pause not confirmed")
    for name in WRITERS:
        mutation(path, record, "stop:" + name, "run", "services", "update", name,
                 "--scaling=0", "--region=" + REGION, "--quiet")
    assert_stopped()
    record["writers_stopped"] = True
    save(path, record)


def assert_stopped():
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        serving(service)
        annotations = service["metadata"].get("annotations", {})
        require(annotations.get("run.googleapis.com/scalingMode") == "manual"
                and str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0",
                "writer is not stopped")


def drain(engine, timeout=1800):
    from sqlalchemy import text
    path, record = load()
    require(record["writers_stopped"], "writers must be stopped before drain")
    assert_stopped()
    deadline = time.monotonic() + timeout
    with engine.connect() as connection:
        require(connection.execute(text("SELECT current_database()")).scalar_one() == "orders",
                "unexpected maintenance database")
        while True:
            remaining = connection.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND backend_type='client backend'"
            )).scalar_one()
            connection.rollback()
            if remaining == 0:
                break
            require(time.monotonic() < deadline, "database clients did not drain")
            time.sleep(5)
    assert_stopped()
    record["database_drained"] = True
    save(path, record)


def show(image):
    path, record = load()
    require(image.startswith(f"{REGION}-docker.pkg.dev/{PROJECT}/backend/") and "@sha256:" in image,
            "immutable maintenance image required")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    routes = verify_captured_tags(record, "web-prod", service)
    require(serving(service, [item["tag"] for item in routes])
            == record["services"]["web-prod"]["revision"], "portal revision changed")
    replacement = copy.deepcopy(service)
    replacement.pop("status", None)
    metadata = replacement["metadata"]
    metadata.setdefault("labels", {})["sawa-maintenance-run"] = os.environ["GITHUB_RUN_ID"]
    for key in ("creationTimestamp", "generation", "resourceVersion", "selfLink", "uid"):
        metadata.pop(key, None)
    template = replacement["spec"]["template"]
    template["metadata"]["name"] = "web-prod-maint-" + record["run"]
    record["maintenance_revision"] = template["metadata"]["name"]
    record["maintenance_image"] = image
    save(path, record)
    containers = template["spec"]["containers"]
    container = containers[0]
    # Sidecars must not keep database connections or start business work during maintenance.
    template["spec"]["containers"] = [container]
    template["metadata"].get("annotations", {}).pop("run.googleapis.com/container-dependencies", None)
    container["image"] = image
    container["command"] = ["node"]
    container["args"] = ["/sawa-maintenance/server.cjs"]
    container["startupProbe"] = {"httpGet": {"path": "/healthz", "port": 8080}}
    container.pop("livenessProbe", None)
    replacement["spec"]["traffic"] = [{"latestRevision": True, "percent": 100}]
    target = path.with_name("maintenance-service.json")
    target.write_text(json.dumps(replacement))
    target.chmod(0o600)
    try:
        mutation(path, record, "show-maintenance", "run", "services", "replace", str(target),
                 "--region=" + REGION, "--quiet")
    finally:
        target.unlink(missing_ok=True)
    observed = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    revision = serving(observed)
    require(revision == record["maintenance_revision"], "maintenance revision identity mismatch")
    detail = cloud('run', 'revisions', 'describe', revision, '--region=' + REGION)
    require(revision_images(detail) == [image], 'maintenance image mismatch')
    record['maintenance_uid'] = detail['metadata']['uid']
    record["maintenance_visible"] = True
    save(path, record)
    verify_display()


def restore_frontend_entrypoint(service):
    _, record = load()
    require(record.get("portal_bootstrap_complete") is True, "completed bootstrap required")
    require(serving(service) == record["maintenance_revision"], "maintenance portal changed")
    original = record["services"]["web-prod"]
    revision = cloud("run", "revisions", "describe", original["revision"], "--region=" + REGION)
    require(revision["metadata"]["uid"] == original["uid"] and revision_images(revision) == original["images"],
            "original portal revision changed")
    old = revision["spec"]["containers"]
    require(old and old[0].get("ports"), "original ingress container missing")
    service["spec"]["template"]["spec"]["containers"] = copy.deepcopy(old)
    annotations = service["spec"]["template"]["metadata"].setdefault("annotations", {})
    dependencies = revision["metadata"].get("annotations", {}).get("run.googleapis.com/container-dependencies")
    if dependencies is not None:
        annotations["run.googleapis.com/container-dependencies"] = dependencies
    return service


def verify_display():
    _, record = load()
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, record.get("candidate_tag")) == record.get("maintenance_revision"),
            "maintenance traffic changed")
    if record.get('maintenance_uid') or record.get('maintenance_image'):
        revision = cloud('run', 'revisions', 'describe', record['maintenance_revision'], '--region=' + REGION)
        require((not record.get('maintenance_uid') or revision['metadata']['uid'] == record['maintenance_uid'])
                and (not record.get('maintenance_image')
                     or revision_images(revision) == [record['maintenance_image']]),
                'maintenance revision identity changed')
    for route in ("/", "/login", "/api/auth/me"):
        try:
            response = urlopen(service["status"]["url"] + route, timeout=60)
        except HTTPError as error:
            response = error
        with response:
            require(response.code == 503 and response.headers.get("X-Sawa-Maintenance") == "1",
                    "maintenance response not confirmed")


def candidate_url():
    path, record = load()
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, record.get("candidate_tag")) == record["maintenance_revision"],
            "maintenance traffic must remain active")
    revision = service["status"]["latestReadyRevisionName"]
    require(revision not in {record["maintenance_revision"], record["services"]["web-prod"]["revision"]},
            "new candidate revision required")
    expected = sorted([os.environ["IMAGE"], os.environ["SAWA_AGENT_RUNTIME_IMAGE"]])
    require(all("@sha256:" in image for image in expected), "immutable candidate images required")
    detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
    require(sorted(revision_images(detail)) == expected, "candidate images differ from release")
    tag = "verify-" + record["run"]
    record.update(candidate_revision=revision, candidate_tag=tag,
                  candidate_images=expected, candidate_uid=detail["metadata"]["uid"])
    save(path, record)
    cloud("run", "services", "update-traffic", "web-prod", "--set-tags=" + tag + "=" + revision,
          "--to-revisions=" + record["maintenance_revision"] + "=100", "--region=" + REGION, "--quiet")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, tag) == record["maintenance_revision"], "candidate changed public traffic")
    tagged = [item for item in service["status"]["traffic"] if item.get("tag") == tag]
    require(len(tagged) == 1 and tagged[0].get("revisionName") == revision
            and not tagged[0].get("percent"), "candidate route mismatch")
    url = tagged[0]["url"]
    require(url.startswith("https://") and url.endswith(".a.run.app"), "unexpected candidate URL")
    record["candidate_url"] = url
    save(path, record)
    verify_display()
    return url


def promote():
    path, record = load()
    verify_display()
    require(record.get("writers_verified_open") is True, "writer activation must be verified")
    require(record.get("candidate_revision") and record.get("candidate_url"), "verified candidate required")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(service["status"]["latestReadyRevisionName"] == record["candidate_revision"],
            "candidate changed before promotion")
    detail = cloud("run", "revisions", "describe", record["candidate_revision"], "--region=" + REGION)
    require(detail["metadata"]["uid"] == record["candidate_uid"]
            and sorted(revision_images(detail)) == record["candidate_images"], "candidate identity changed")
    cloud("run", "services", "update-traffic", "web-prod",
          "--to-revisions=" + record["candidate_revision"] + "=100",
          "--remove-tags=" + record["candidate_tag"], "--region=" + REGION, "--quiet")
    require(serving(cloud("run", "services", "describe", "web-prod", "--region=" + REGION))
            == record["candidate_revision"], "promotion not confirmed")
    record["candidate_checks_passed"] = True
    save(path, record)


def hold_on_failure():
    path, record = load()
    if record.get("reopened"):
        return
    maintenance = record.get("maintenance_revision")
    require(maintenance, "maintenance revision is not recorded; inspect original run")
    failures = []
    try:
        service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
        current = serving(service, record.get("candidate_tag"))
    except Exception:
        current = None
        failures.append("portal-inspection")
    if current is not None and current not in {maintenance, record.get("candidate_revision")}:
        current = None
        failures.append("unrelated-portal-change")
    arguments = ["run", "services", "update-traffic", "web-prod",
                 "--to-revisions=" + maintenance + "=100", "--region=" + REGION, "--quiet"]
    if record.get("candidate_tag"):
        arguments.append("--remove-tags=" + record["candidate_tag"])
    try:
        require(current is not None, "portal ownership could not be checked")
        mutation(path, record, "failure-hold-portal", *arguments)
        cloud("run", "services", "update", "web-prod", "--update-labels=sawa-maintenance-run=" + record["run"],
              "--region=" + REGION, "--quiet")
        verify_display()
    except Exception:
        failures.append("portal-maintenance")
    for name in WRITERS:
        try:
            mutation(path, record, "failure-stop:" + name, "run", "services", "update", name,
                     "--scaling=0", "--region=" + REGION, "--quiet")
        except Exception:
            failures.append(name)
    try:
        assert_stopped()
    except Exception:
        failures.append("writer-stop-verification")
    record["failure_hold_unconfirmed"] = failures
    record["held_after_failure"] = not failures
    save(path, record)
    require(not failures, "maintenance recovery incomplete: " + ", ".join(failures))


def resume_writer(name, expected_image):
    _, record = load()
    require(name in WRITERS and record["database_drained"], "verified maintenance required")
    service = cloud("run", "services", "describe", name, "--region=" + REGION)
    revision_name = serving(service)
    require(revision_name != record["services"][name]["revision"], "never reopen original writer on migrated DB")
    revision = cloud("run", "revisions", "describe", revision_name, "--region=" + REGION)
    images = [item["image"] for item in revision["spec"]["containers"]]
    require(expected_image and (expected_image in images or expected_image in revision_images(revision)),
            "new writer image mismatch")
    require(all(any(entry.get("name") == "SAWA_RELEASE_READ_ONLY" and entry.get("value") == "1"
                    for entry in container.get("env", []))
                for container in revision["spec"]["containers"]), "writer read-only gate missing")
    annotations = service["metadata"].get("annotations", {})
    require(annotations.get("run.googleapis.com/scalingMode") == "manual"
            and str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0",
            "writer was not held stopped")
    cloud("run", "services", "update", name, "--scaling=auto", "--region=" + REGION, "--quiet")
    observed = cloud("run", "services", "describe", name, "--region=" + REGION)
    require(serving(observed) == revision_name,
            "writer traffic changed while reopening")
    require(observed["metadata"].get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic") == "automatic",
            "writer automatic scaling not confirmed")


def open_writers():
    path, record = load()
    verify_display()
    require(record.get("candidate_url") and record.get("portal_bootstrap_complete"),
            "checked portal candidate and database bootstrap required")
    candidate = cloud("run", "revisions", "describe", record["candidate_revision"], "--region=" + REGION)
    require(candidate["metadata"]["uid"] == record["candidate_uid"]
            and sorted(revision_images(candidate)) == record["candidate_images"], "candidate identity changed")
    from registry_image_digest import resolve_image_digest
    expected = {name: resolve_image_digest(os.environ[key]) for name, key in (
        ("worker-prod", "PRODUCTION_BACKEND_IMAGE"), ("ocr-pipeline-prod", "PRODUCTION_PRODUCER_IMAGE"))}
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        revision = serving(service)
        require(revision != record["services"][name]["revision"], "original writer must not reopen")
        detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
        images = revision_images(detail)
        require(images == [expected[name]], "writer is not the verified release image")
        containers = detail["spec"]["containers"]
        require(len(containers) == 1 and any(entry.get("name") == "SAWA_RELEASE_READ_ONLY"
                and entry.get("value") == "1" for entry in containers[0].get("env", [])),
                "read-only writer required before opening")
        mutation(path, record, "open:" + name, "run", "services", "update", name,
                 "--remove-env-vars=SAWA_RELEASE_READ_ONLY", "--region=" + REGION, "--quiet")
        observed = cloud("run", "services", "describe", name, "--region=" + REGION)
        ready = cloud("run", "revisions", "describe", serving(observed), "--region=" + REGION)
        require(revision_images(ready) == images, "writer image changed while opening")
        require(any(item.get("type") == "Ready" and item.get("status") == "True"
                    for item in ready.get("status", {}).get("conditions", [])), "opened writer is not ready")
        require(not any(entry.get("name") == "SAWA_RELEASE_READ_ONLY"
                    for container in ready["spec"]["containers"] for entry in container.get("env", [])),
                "writer read-only gate removal not confirmed")
    record["writers_verified_open"] = True
    save(path, record)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("begin", "capture", "resume-start", "stop", "show", "verify-display", "candidate-url", "promote", "hold-on-failure", "open-writers", "download-start", "download-database", "drain", "bootstrap-complete", "require-bootstrap", "resume-writer"))
    parser.add_argument("--image")
    parser.add_argument("--service")
    parser.add_argument("--original-run")
    args = parser.parse_args()
    if args.action.startswith("download-"):
        download_record(args.action.removeprefix("download-"))
    elif args.action == 'begin':
        begin(args.image or '', args.original_run or '')
    elif args.action == "capture":
        capture()
    elif args.action == "resume-start":
        resume_start(args.original_run)
    elif args.action == "stop":
        stop_writers()
    elif args.action == "show":
        show(args.image or "")
    elif args.action == "verify-display":
        verify_display()
    elif args.action == "candidate-url":
        print(candidate_url())
    elif args.action == "promote":
        promote()
    elif args.action == "open-writers":
        open_writers()
    elif args.action == "hold-on-failure":
        hold_on_failure()
    elif args.action == "resume-writer":
        resume_writer(args.service, args.image)
    elif args.action in {"bootstrap-complete", "require-bootstrap"}:
        path, record = load()
        require(record.get("database_drained") and record.get("maintenance_revision"), "verified maintenance required")
        if args.action == "bootstrap-complete":
            assert_stopped()
            record["portal_bootstrap_complete"] = True
            save(path, record)
        else:
            require(record.get("portal_bootstrap_complete") is True, "portal bootstrap not completed")
    else:
        from run_prod_schema_adoption import production_database
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        config = production_database()
        engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
            password=config.db_password, host="127.0.0.1", port=5432, database="orders"),
            hide_parameters=True, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
        try:
            drain(engine)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
