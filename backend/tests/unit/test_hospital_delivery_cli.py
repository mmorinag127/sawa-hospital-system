"""Strict argv checks and opt-in local gcloud parser checks; no cloud operations."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("hospital_delivery_cli_config", ROOT / "scripts/hospital_delivery_config.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)

CASES = [
    (("tasks", "queues", "--location=asia-northeast2", "hospital-ingest-stg"),
     ["tasks", "queues"], "hospital-ingest-stg", ["--location=asia-northeast2"], "roles/cloudtasks.viewer"),
    (("run", "services", "--region=asia-northeast2", "worker-stg"),
     ["run", "services"], "worker-stg", ["--region=asia-northeast2"], "roles/run.invoker"),
    (("iam", "service-accounts", "hosp-ingest-task-stg@sawahospitalsystem.iam.gserviceaccount.com"),
     ["iam", "service-accounts"], "hosp-ingest-task-stg@sawahospitalsystem.iam.gserviceaccount.com", [],
     "roles/iam.serviceAccountUser"),
]


def commands(case, verb):
    resource, group, name, flags, role = case
    options = []
    if verb == "add-iam-policy-binding":
        options = [f"--member=serviceAccount:{config.RUNTIME}", f"--role={role}"]
        if group[0] != "tasks":
            options.append("--condition=None")
    return (*resource, verb, *options), [*group, verb, name, *flags, *options]


@pytest.mark.parametrize("case", CASES, ids=["tasks", "run", "iam"])
@pytest.mark.parametrize("verb", ["get-iam-policy", "add-iam-policy-binding"])
def test_exact_resource_command_argv(monkeypatch, case, verb):
    received = []
    monkeypatch.setattr(config, "gcloud", lambda *args: received.append(list(args)))
    source, expected = commands(case, verb)
    config.resource_command(*source)
    assert received == [expected]


@pytest.mark.parametrize("case", CASES, ids=["tasks", "run", "iam"])
def test_ensure_binding_uses_exact_get_add_get(monkeypatch, case):
    resource, _, _, _, role = case
    member = f"serviceAccount:{config.RUNTIME}"
    calls = []
    def cloud(*args):
        calls.append(list(args))
        return {"bindings": [{"role": role, "members": [member]}]} if len(calls) == 3 else {}
    monkeypatch.setattr(config, "gcloud", cloud)
    config.ensure_binding(resource, member, role)
    get = commands(case, "get-iam-policy")[1]
    add = commands(case, "add-iam-policy-binding")[1]
    assert calls == [get, add, get]


@pytest.mark.parametrize("args", [("tasks", "queues", "get-iam-policy"),
    ("tasks", "queues", "--location=asia-northeast2", "get-iam-policy"),
    ("tasks", "queues", "hospital-ingest-stg", "delete")])
def test_invalid_command_stops_before_gcloud(monkeypatch, args):
    def forbidden(*args):
        pytest.fail("invalid command reached gcloud")
    monkeypatch.setattr(config, "gcloud", forbidden)
    with pytest.raises(config.Blocked):
        config.resource_command(*args)


@pytest.mark.skipif(os.environ.get("HOSPITAL_DELIVERY_CLI_HELP_TEST") != "1", reason="opt-in local gcloud help")
@pytest.mark.parametrize("case", CASES, ids=["tasks", "run", "iam"])
@pytest.mark.parametrize("verb", ["get-iam-policy", "add-iam-policy-binding"])
def test_real_gcloud_help_parser(monkeypatch, tmp_path, case, verb):
    executable = shutil.which("gcloud")
    assert executable, "gcloud is required for explicit parser verification"
    source, expected = commands(case, verb)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CLOUDSDK_", "GOOGLE_", "GCLOUD_"))}
    env.update(CLOUDSDK_CONFIG=str(tmp_path / "gcloud"), CLOUDSDK_CORE_DISABLE_PROMPTS="1",
               CLOUDSDK_CORE_DISABLE_USAGE_REPORTING="true", CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK="true",
               CLOUDSDK_PAGER="cat", PAGER="cat")
    # Route the production wrapper's exact argv only to local help, never execution.
    run = subprocess.run
    def help_only(argv, **kwargs):
        assert argv == ["gcloud", *expected, f"--project={config.PROJECT}", "--quiet", "--format=json"]
        result = run([executable, *argv[1:], "--help"], env=env, capture_output=True, text=True, timeout=30)
        (tmp_path / "help.txt").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        assert "unrecognized arguments" not in result.stderr
        assert verb in result.stdout
        return subprocess.CompletedProcess(argv, 0, "{}", "")
    monkeypatch.setattr(config.subprocess, "run", help_only)
    config.resource_command(*source)
