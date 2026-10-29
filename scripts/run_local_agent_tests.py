"""Verify native processes in an owned local Linux/network namespace."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

from local_hospital_test_runner import ROOT, clean_tree, focused_staging, git, reject_hosted_runner, staging_scope_base


def select_test_targets():
    full = {"mode": "full", "targets": ["experiments/agent_assistant/tests"]}
    if not focused_staging():
        return full
    try:
        base = staging_scope_base()
        paths = [path for path in git("diff", "--name-only", "-z", base, "HEAD").split("\0") if path]
    except subprocess.CalledProcessError:
        return full
    if not paths or any(
        path.startswith("experiments/agent_assistant/")
        or path.startswith("agent_runtime/baselines/")
        or (path.startswith("agent_runtime/native/") and path not in {
            "agent_runtime/native/shift/backend/shift_scheduler_backend/schema_contract.py",
            "agent_runtime/native/school-lunch/backend/src/migration_catalog.py",
        })
        or path in {"agent_runtime/Dockerfile", "agent_runtime/release.json", "local-checks/agent-tests.Dockerfile"}
        or path == "frontend/public/assistant-workflow.css"
        or "requirements" in Path(path).name or Path(path).name == "pyproject.toml"
        for path in paths
    ):
        return full
    names = (
        "test_native_contract_comparison.py", "test_prepare_packaged.py", "test_staging_runtime.py",
        "test_native_lifecycle.py", "test_runtime_gateway.py", "test_fax_workflow.py",
        "test_hospital_output_completion.py",
        "test_native_workflows.py::test_packaged_api_auth_and_extensions_with_latest_contracts",
    )
    targets = ["experiments/agent_assistant/tests/" + name for name in names]
    if any(not (ROOT / target.split("::", 1)[0]).is_file() for target in targets):
        raise ValueError("Focused native verification target is missing")
    return {"mode": "focused-read-and-startup", "targets": targets, "base_sha": base, "changed_paths": paths}


def docker(*args, **kwargs):
    return subprocess.run(["docker", *map(str, args)], check=True, **kwargs)


def inspect(identity):
    return json.loads(docker("inspect", identity, capture_output=True, text=True).stdout)[0]


def cleanup_owned_containers(names, owner, source, *, evidence=None):
    errors = []
    for name in names:
        try:
            found = docker("container", "ls", "--all", "--filter", "name=^/" + name + "$",
                           "--format", "{{.ID}}", capture_output=True, text=True).stdout.strip()
            if not found:
                continue
            actual = inspect(name)
            if (actual["Config"]["Labels"].get("sawa.local_run") != owner
                    or actual["Config"]["Labels"].get("sawa.git_sha") != source
                    or not re.fullmatch(r"[0-9a-f]{64}", actual["Id"])):
                raise RuntimeError("Owned test container identity changed; cleanup refused")
            if evidence is not None:
                try:
                    logs = subprocess.run(["docker", "logs", "--timestamps", actual["Id"]],
                                          capture_output=True, text=True)
                    (evidence / (actual["Id"] + ".log")).write_text(logs.stdout + logs.stderr)
                    if logs.returncode:
                        raise RuntimeError("Owned test container log capture failed")
                except BaseException as exc:
                    errors.append(("logs:" + name, exc))
            docker("stop", "--time", "30", actual["Id"])
            if inspect(actual["Id"])["State"]["Running"]:
                raise RuntimeError("Owned test container did not stop")
            docker("rm", "--volumes", actual["Id"])
            if docker("container", "ls", "--all", "--filter", "name=^/" + name + "$",
                      "--format", "{{.ID}}", capture_output=True, text=True).stdout.strip():
                raise RuntimeError("Owned test container remains after cleanup")
        except BaseException as exc:
            # Each independently owned container still gets its cleanup attempt.
            errors.append((name, exc))
    return errors


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError("Native tests require a clean fixed source")
    source, owner = git("rev-parse", "HEAD"), uuid4().hex
    selection = select_test_targets()
    image = os.environ["SAWA_LOCAL_TEST_IMAGE_AGENT_RUNTIME"]
    base = inspect(image)
    if (base["Os"] != "linux" or base["Architecture"] != "amd64"
            or base["Config"]["Labels"].get("sawa.git_sha") != source):
        raise ValueError("Native runtime image source/platform mismatch")
    evidence = Path(os.environ["SAWA_LOCAL_EVIDENCE_DIR"]) / "agent-linux"
    evidence.mkdir(exist_ok=False)
    prefix = "sawa-native-test-" + owner
    derived, database, test = prefix + ":image", prefix + "-pg", prefix + "-run"
    labels = ["--label", "sawa.local_run=" + owner, "--label", "sawa.git_sha=" + source]
    docker("build", "--platform", "linux/amd64", "--build-arg", "BASE_IMAGE=" + image,
           "-f", ROOT / "local-checks/agent-tests.Dockerfile", "-t", derived, ROOT)
    if inspect(image)["Id"] != base["Id"]:
        raise ValueError("Native runtime base changed during test-image preparation")
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    dev_image = inspect(derived)
    postgres = inspect("postgres:16")
    if postgres["Os"] != "linux":
        raise ValueError("Owned PostgreSQL requires a local Linux image")
    record = {"source_sha": source, "runtime_image_id": base["Id"],
              "test_image_id": dev_image["Id"], "database_image_id": postgres["Id"], "test_only_dependencies": [
                  "git", "SQLAlchemy==2.1.6", "alembic==1.20.1",
                  "openpyxl==3.1.5", "et-xmlfile==2.0.0"], "run_id": owner,
              "test_selection": selection}
    (evidence / "source.json").write_text(json.dumps(record, indent=2))
    try:
        docker("run", "-d", "--name", database, *labels, "--network", "none", "--memory", "256m",
               "-e", "POSTGRES_USER=sawa_agent_local", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
               "-e", "POSTGRES_DB=agent_trial_" + owner, postgres["Id"],
               "postgres", "-c", "shared_buffers=16MB", "-c", "max_connections=20")
        deadline = time.monotonic() + 30
        while subprocess.run(["docker", "exec", database, "pg_isready", "-h", "127.0.0.1",
                              "-U", "sawa_agent_local", "-d", "agent_trial_" + owner],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned test PostgreSQL did not become ready")
            time.sleep(0.2)
        actual = json.loads(docker("exec", database, "psql", "-X", "-At", "-h", "127.0.0.1", "-U", "sawa_agent_local",
                                  "-d", "agent_trial_" + owner, "-c",
                                  "SELECT json_build_object('database',current_database(),'role',current_user,"
                                  "'major',current_setting('server_version_num')::int/10000)",
                                  capture_output=True, text=True).stdout)
        if actual != {"database": "agent_trial_" + owner, "role": "sawa_agent_local", "major": 16}:
            raise ValueError("Owned test database identity mismatch")
        args = ["run", "--name", test, *labels, "--platform", "linux/amd64", "--memory", "2g",
                "--cpus", "1", "--network", "container:" + database,
                "-v", f"{ROOT}:{ROOT}:ro", "-v", f"{common}:{common}:ro",
                "-v", f"{evidence}:/evidence", "-w", str(ROOT)]
        environment = {
            "SAWA_AGENT_TEST_POSTGRES_DSN": f"postgresql://sawa_agent_local@127.0.0.1/agent_trial_{owner}",
            "SAWA_AGENT_NATIVE_TESTS": "1", "SAWA_AGENT_GATEWAY_PYTHON": "/opt/agent/gateway/bin/python",
            "SAWA_AGENT_NATIVE_HOSPITAL_PYTHON": "/opt/agent/hospital/bin/python",
            "SAWA_AGENT_NATIVE_SHIFT_PYTHON": "/opt/agent/shift/bin/python",
            "SAWA_AGENT_NATIVE_SCHOOL_LUNCH_PYTHON": "/opt/agent/school-lunch/bin/python",
            "PYTHONPATH": f"{ROOT}:{ROOT}/backend", "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(ROOT),
        }
        for key, value in environment.items():
            args += ["-e", key + "=" + value]
        args += ["--entrypoint", "/opt/agent/gateway/bin/python", dev_image["Id"], "-m", "pytest",
                 *selection["targets"], "-v", "-p", "no:cacheprovider",
                 "-o", "faulthandler_timeout=120",
                 "--basetemp=/tmp/sawa-native-tests", "--junitxml=/evidence/results.xml"]
        result = subprocess.run(["docker", *args])
        record["test_exit_code"] = result.returncode
        return result.returncode
    except subprocess.CalledProcessError as exc:
        record["process_error"] = {"exit_code": exc.returncode,
                                   "stdout": exc.stdout, "stderr": exc.stderr}
        raise
    finally:
        errors = cleanup_owned_containers((test, database), owner, source, evidence=evidence)
        if not clean_tree() or git("rev-parse", "HEAD") != source:
            errors.append(("source", RuntimeError("Native test source changed")))
        record["cleanup_verified"] = not errors
        record["cleanup_errors"] = [{"target": name, "error": type(exc).__name__, "message": str(exc)}
                                    for name, exc in errors]
        (evidence / "source.json").write_text(json.dumps(record, indent=2))
        if errors:
            raise RuntimeError("Native test cleanup or source verification failed") from errors[0][1]


if __name__ == "__main__":
    raise SystemExit(main())
