"""No cloud calls: exercise the exact gcloud argv and deployment handoff."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("hospital_delivery_config", ROOT / "scripts/hospital_delivery_config.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class Cloud:
    def __init__(self):
        self.calls = []
        self.accounts = {config.RUNTIME: self.sa(config.RUNTIME, "100629794303746284837")}
        self.queues = {}
        self.jobs = {}
        self.policies = {}
        self.deny = None
        self.project_policy = {"bindings": [
            {"role": f"roles/{service}.serviceAgent", "members": [
                f"serviceAccount:service-167795504375@gcp-sa-{service}.iam.gserviceaccount.com"]}
            for service in ("cloudtasks", "cloudscheduler")
        ]}

    @staticmethod
    def sa(account, subject):
        return {"email": account, "projectId": config.PROJECT, "uniqueId": subject}

    def run(self, argv, **kwargs):
        assert argv[0] == "gcloud"
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        assert argv[-3:] == [f"--project={config.PROJECT}", "--quiet", "--format=json"]
        args = argv[1:-3]
        self.calls.append(args)
        if self.deny and args[:len(self.deny)] == self.deny:
            return SimpleNamespace(returncode=1, stdout="", stderr="SECRET_SHOULD_NOT_ESCAPE")
        positional = [arg for arg in args if not arg.startswith("--")]
        flags = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("--"))
        p = positional
        value = {}
        if p == ["auth", "list"]:
            value = [{"account": config.DEPLOY}]
        elif p == ["projects", "describe", config.PROJECT]:
            value = {"projectNumber": "167795504375"}
        elif p == ["projects", "get-iam-policy", config.PROJECT]:
            value = self.project_policy
        elif p[:3] == ["run", "services", "describe"]:
            value = {"status": {"url": config.URL}, "spec": {"template": {"spec": {
                "serviceAccountName": config.RUNTIME, "timeoutSeconds": 1800}}}}
        elif "get-iam-policy" in p or "add-iam-policy-binding" in p:
            group, resource, verb, name = p
            assert (group, resource) in {("iam", "service-accounts"), ("run", "services"), ("tasks", "queues")}
            key = (group, resource, name)
            value = self.policies.setdefault(key, {"bindings": []})
            if verb == "add-iam-policy-binding":
                if group == "tasks":
                    assert "condition" not in flags
                else:
                    assert flags["condition"] == "None"
                value["bindings"].append({"role": flags["role"], "members": [flags["member"]]})
        elif p[:2] == ["iam", "service-accounts"]:
            verb = p[2]
            if verb == "list":
                value = list(self.accounts.values())
            elif verb == "create":
                account = config.email(p[3])
                self.accounts[account] = self.sa(account, str(100000000000000000000 + len(self.accounts)))
            elif verb == "describe":
                assert "gcp-sa-" not in p[3], "Google-managed agents must not be described as ordinary SAs"
                value = self.accounts[p[3]]
            else:
                raise AssertionError(p)
        elif p[:2] == ["tasks", "queues"]:
            verb = p[2]
            if verb == "list":
                value = list(self.queues.values())
            elif verb == "create":
                self.queues[p[3]] = {"name": f"projects/{config.PROJECT}/locations/{config.REGION}/queues/{p[3]}",
                    "state": "RUNNING", "rateLimits": {"maxConcurrentDispatches": int(flags["max-concurrent-dispatches"]),
                    "maxDispatchesPerSecond": float(flags["max-dispatches-per-second"])}}
            elif verb == "describe":
                value = self.queues[p[3]]
            else:
                raise AssertionError(p)
        elif p[:2] == ["scheduler", "jobs"]:
            verb = p[2]
            if verb == "list":
                value = list(self.jobs.values())
            elif verb == "create":
                assert p[3] == "http"
                self.jobs[p[4]] = {"name": f"projects/{config.PROJECT}/locations/{config.REGION}/jobs/{p[4]}",
                    "state": "ENABLED", "schedule": flags["schedule"], "timeZone": flags["time-zone"],
                    "attemptDeadline": flags["attempt-deadline"], "httpTarget": {"uri": flags["uri"],
                    "httpMethod": flags["http-method"], "oidcToken": {"serviceAccountEmail": flags["oidc-service-account-email"],
                    "audience": flags["oidc-token-audience"]}}}
            elif verb == "describe":
                value = self.jobs[p[3]]
            else:
                raise AssertionError(p)
        else:
            raise AssertionError(p)
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/develop",
        "GITHUB_REPOSITORY": "mmorinag127/sawa-hospital-system", "PROJECT_ID": config.PROJECT,
        "REGION": config.REGION, "WORKER_SERVICE": config.SERVICE, "WORKER_URL": config.URL,
        "GITHUB_ENV": str(tmp_path / "github-env")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.chdir(tmp_path)
    fake = Cloud()
    monkeypatch.setattr(config.subprocess, "run", fake.run)
    return fake


def test_bootstrap_contract_and_least_privilege(cloud):
    env, evidence = config.bootstrap()
    assert len(env) == 31
    assert len(cloud.queues) == 2
    assert len(cloud.jobs) == 2
    assert len(cloud.accounts) == 5
    for flow, prefix in config.FLOWS:
        task = config.email(f"hosp-{flow}-task-stg")
        drain = config.email(f"hosp-{flow}-drain-stg")
        assert env[prefix + "WORKER_SUBJECT"] == cloud.accounts[task]["uniqueId"]
        assert env[prefix + "DRAIN_SUBJECT"] == cloud.accounts[drain]["uniqueId"]
        assert env[prefix + "SERVICE_ACCOUNT_EMAIL"] == task
        assert env[prefix + "WORKER_EMAIL"] == task
        assert env[prefix + "DISPATCH_DEADLINE_SECONDS"] == "1800"
        assert env[prefix + "TARGET_URL"] == env[prefix + "AUDIENCE"] == config.URL
        assert evidence["flows"][flow]["drain_uri"].endswith(f"/worker/{flow}-deliveries/drain?limit=10")
    assert int(env["HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS"]) > 1800
    writes = [args for args in cloud.calls if "add-iam-policy-binding" in args]
    deploy_grants = [args for args in writes if f"--member=serviceAccount:{config.DEPLOY}" in args]
    assert len(deploy_grants) == 2
    assert all(args[:2] == ["iam", "service-accounts"] and "drain-stg@" in " ".join(args)
               and "--role=roles/iam.serviceAccountUser" in args for args in deploy_grants)
    assert all("roles/cloudtasks.taskRunner" not in " ".join(args) for args in cloud.calls)
    assert not any(args[:2] == ["iam", "roles"] for args in cloud.calls)
    assert not any(args[:2] == ["projects", "add-iam-policy-binding"] for args in cloud.calls)
    queue_grants = [args for args in writes if args[:2] == ["tasks", "queues"]]
    assert len(queue_grants) == 4
    assert {a for args in queue_grants for a in args if a.startswith("--role=")} == {
        "--role=roles/cloudtasks.enqueuer", "--role=roles/cloudtasks.viewer"}
    assert all("school" not in " ".join(args) for args in cloud.calls)


def test_rerun_verifies_without_recreating(cloud):
    first = config.bootstrap()
    cloud.calls.clear()
    assert config.bootstrap() == first
    assert not any("create" in args or "add-iam-policy-binding" in args for args in cloud.calls)


@pytest.mark.parametrize("key,value", [("GITHUB_ACTIONS", "false"), ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_REPOSITORY", "mmorinag127/sawa-platform"), ("PROJECT_ID", "other"),
    ("WORKER_SERVICE", "worker"), ("WORKER_URL", "https://other"), ("REGION", "us-central1"),
    ("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT", "other")])
def test_wrong_context_never_calls_cloud(cloud, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(config.Blocked):
        config.bootstrap()
    assert not cloud.calls


@pytest.mark.parametrize("denied", [["scheduler", "jobs", "list"], ["scheduler", "jobs", "create"],
    ["iam", "service-accounts", "list"], ["tasks", "queues", "list"],
    ["iam", "service-accounts", "add-iam-policy-binding"]])
def test_denied_authority_blocks_and_redacts(cloud, tmp_path, capsys, denied):
    cloud.deny = denied
    assert config.main() == 1
    assert not (tmp_path / "github-env").exists()
    status = (tmp_path / "hospital-delivery-artifacts/status.json").read_text()
    assert "blocked" in status
    assert "SECRET_SHOULD_NOT_ESCAPE" not in status + capsys.readouterr().err
    assert not any("deploy" in args for args in cloud.calls)


@pytest.mark.parametrize("change", ["subject", "project", "disabled", "queue", "job", "agent"])
def test_descriptor_drift_blocks(cloud, change):
    config.bootstrap()
    account = cloud.accounts[config.email("hosp-ingest-task-stg")]
    if change == "subject":
        account.pop("uniqueId")
    elif change == "project":
        account["projectId"] = "other"
    elif change == "disabled":
        account["disabled"] = True
    elif change == "queue":
        cloud.queues["hospital-ingest-stg"]["httpTarget"] = {"uriOverride": {"host": "other"}}
    elif change == "job":
        cloud.jobs["hospital-ingest-drain-stg"]["httpTarget"]["oidcToken"]["audience"] = "other"
    else:
        cloud.project_policy["bindings"].clear()
    with pytest.raises(config.Blocked):
        config.bootstrap()


def test_verified_artifact_equals_handoff(cloud, tmp_path):
    assert config.main() == 0
    artifact = (tmp_path / "hospital-delivery-artifacts/verified-env.txt").read_text().strip()
    assert (tmp_path / "github-env").read_text() == f"HOSPITAL_DELIVERY_ENV={artifact}\n"
    assert json.loads((tmp_path / "hospital-delivery-artifacts/status.json").read_text())["status"] == "verified"


def test_workflow_gates_before_deploy_and_preserves_env():
    text = (ROOT / ".github/workflows/deploy-stg.yml").read_text()
    bootstrap = text.index("run: python3 scripts/hospital_delivery_config.py")
    deploy = text.index("- name: Deploy worker-stg")
    handoff = text.index('test "$(cat hospital-delivery-artifacts/verified-env.txt)" = "$HOSPITAL_DELIVERY_ENV"')
    invocation = text.index('./scripts/deploy_worker_prod_with_checks.sh "$IMAGE" "$ORDER_ID"')
    assert bootstrap < deploy < handoff < invocation
    assert ',${HOSPITAL_DELIVERY_ENV}"' in text
    assert "SCHOOL_LUNCH_AUTOMATION_AUTH_SUBJECT=${SCHOOL_LUNCH_AUTOMATION_AUTH_SUBJECT}" in text
    assert "GCP_WORKLOAD_IDENTITY_PROVIDER_STG" in text


def test_generated_env_accepted_by_both_production_config_readers(cloud, monkeypatch):
    env, _ = config.bootstrap()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    # Only DB-backed dependencies are isolated; execute the unchanged config readers.
    monkeypatch.setitem(sys.modules, "src.services.workflow_ocr_delivery_service", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "src.services.workflow_ocr_delivery_execution_service",
                        SimpleNamespace(execution_mutex=None))
    for filename in ("ingest_delivery_dispatch_service", "workflow_ocr_delivery_dispatch_service"):
        spec = importlib.util.spec_from_file_location(filename, ROOT / f"backend/src/services/{filename}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = module.required_config()
        assert observed["TARGET_URL"] == config.URL
        assert observed["DISPATCH_DEADLINE_SECONDS"] == "1800"


def test_iam_grants_are_exactly_resource_scoped(cloud):
    config.bootstrap()
    expected = {}
    for flow, _ in config.FLOWS:
        task = config.email(f"hosp-{flow}-task-stg")
        drain = config.email(f"hosp-{flow}-drain-stg")
        expected[("iam", "service-accounts", task)] = {
            ("roles/iam.serviceAccountOpenIdTokenCreator", "serviceAccount:service-167795504375@gcp-sa-cloudtasks.iam.gserviceaccount.com"),
            ("roles/iam.serviceAccountUser", f"serviceAccount:{config.RUNTIME}")}
        expected[("iam", "service-accounts", drain)] = {
            ("roles/iam.serviceAccountOpenIdTokenCreator", "serviceAccount:service-167795504375@gcp-sa-cloudscheduler.iam.gserviceaccount.com"),
            ("roles/iam.serviceAccountUser", f"serviceAccount:{config.DEPLOY}")}
        expected[("tasks", "queues", f"hospital-{flow}-stg")] = {
            (role, f"serviceAccount:{config.RUNTIME}")
            for role in ("roles/cloudtasks.viewer", "roles/cloudtasks.enqueuer")}
    expected[("run", "services", config.SERVICE)] = {
        ("roles/run.invoker", f"serviceAccount:{config.email(f'hosp-{flow}-{kind}-stg')}")
        for flow, _ in config.FLOWS for kind in ("task", "drain")}
    assert {resource: {(item["role"], member) for item in policy["bindings"] for member in item["members"]}
            for resource, policy in cloud.policies.items()} == expected


def test_malformed_list_is_not_absence(cloud, monkeypatch):
    real = cloud.run
    def invalid(argv, **kwargs):
        if argv[1:4] == ["scheduler", "jobs", "list"]:
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        return real(argv, **kwargs)
    monkeypatch.setattr(config.subprocess, "run", invalid)
    with pytest.raises(config.Blocked, match="valid resource list"):
        config.bootstrap()
    assert not any("create" in args for args in cloud.calls)
