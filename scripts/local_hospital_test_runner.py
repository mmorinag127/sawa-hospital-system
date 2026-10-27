#!/usr/bin/env python3
"""Run hospital regression commands locally and create a manually submitted receipt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from local_receipt_signing import sign, verify_signed


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/local_hospital_test_manifest.json"


def focused_staging():
    mode = os.environ.get('SAWA_LOCAL_TEST_MODE')
    if mode not in (None, 'full', 'impact', 'focused'):
        raise ValueError('Unknown local test mode')
    branch = git('branch', '--show-current')
    return mode == 'focused' and bool(branch) and not branch.startswith('release/prod-')


def frontend_scope(group):
    if group not in ('config', 'e2e'):
        raise ValueError('Unknown frontend verification group')
    if not focused_staging():
        return None
    value = json.loads((ROOT / 'infra/ci/staging_frontend_scope.json').read_text())
    if (not isinstance(value, dict) or set(value) != {'version', 'scope', 'config', 'e2e'}
            or type(value['version']) is not int or value['version'] != 1
            or value['scope'] != 'staging-core-and-page-repair'):
        raise ValueError('Invalid staging frontend scope')
    for kind in ('config', 'e2e'):
        modules = value[kind]
        if (not isinstance(modules, list) or not modules
                or any(not isinstance(name, str) for name in modules)
                or len(set(modules)) != len(modules)):
            raise ValueError('Invalid staging frontend module list')
        for name in modules:
            if (not isinstance(name, str) or not name.startswith('tests/')
                    or '..' in Path(name).parts or not (ROOT / 'frontend' / name).is_file()
                    or (kind == 'e2e' and not name.endswith('.spec.ts'))
                    or (kind == 'config' and not name.endswith(('.test.cjs', '.test.mjs', '.test.js')))):
                raise ValueError('Invalid staging frontend module')
    return value[group]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject_hosted_runner() -> None:
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        raise SystemExit("local hospital tests refuse GITHUB_ACTIONS=true or CI=true")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def clean_tree() -> bool:
    return not git("status", "--porcelain")


def require_frontend_dependencies() -> None:
    modules = ROOT / "frontend" / "node_modules"
    required = (".package-lock.json", ".bin/eslint", ".bin/tsc", ".bin/next",
                "@playwright/test/package.json")
    missing = [name for name in required if not (modules / name).is_file()]
    if missing:
        raise SystemExit("prepare frontend dependencies with npm ci before the local batch: "
                         + ", ".join(missing))


def validate_receipt(receipt: dict, expected_sha: str, expected_definition_hash: str, *, require_full: bool = False) -> None:
    if type(receipt) is not dict:
        raise ValueError("receipt fields do not match the local receipt contract")
    required = {"receipt_version", "owner_attestation", "source_sha_start", "source_sha_end", "clean_before", "clean_after", "definition_sha256", "commands"}
    if receipt.get("receipt_version") == 2:
        required.add("backend_selection")
    if type(receipt) is not dict or set(receipt) != required:
        raise ValueError("receipt fields do not match the local receipt contract")
    if type(receipt["receipt_version"]) is not int or receipt["receipt_version"] not in (1, 2) or receipt["owner_attestation"] != "manual":
        raise ValueError("receipt must be a supported manual owner attestation")
    if receipt["receipt_version"] == 2:
        selection = receipt["backend_selection"]
        if require_full and (not isinstance(selection, dict) or selection.get("mode") != "full"):
            raise ValueError("production requires full backend execution")
        if (not isinstance(selection, dict) or set(selection) != {"source_sha", "mode", "executed", "reused", "report_sha256"}
                or selection["source_sha"] != expected_sha or selection["mode"] not in ("full", "impact", "focused")
                or any(type(selection[key]) is not int or selection[key] < 0 for key in ("executed", "reused"))
                or not selection["executed"] + selection["reused"]
                or (selection["mode"] == "full" and selection["reused"])
                or not isinstance(selection["report_sha256"], str)
                or len(selection["report_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in selection["report_sha256"])):
            raise ValueError("invalid signed backend selection evidence")
    if receipt["source_sha_start"] != expected_sha or receipt["source_sha_end"] != expected_sha or receipt["definition_sha256"] != expected_definition_hash:
        raise ValueError("receipt source SHA or command definition hash does not match")
    if receipt["clean_before"] is not True or receipt["clean_after"] is not True:
        raise ValueError("receipt requires a clean tree before and after local tests")
    if not all(isinstance(receipt[key], bool) for key in ("clean_before", "clean_after")):
        raise ValueError("receipt clean flags must be booleans")
    if len(expected_sha) != 40 or any(ch not in "0123456789abcdef" for ch in expected_sha):
        raise ValueError("expected source SHA must be a full lowercase Git SHA")
    if len(expected_definition_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_definition_hash):
        raise ValueError("expected definition hash must be SHA256")
    commands = receipt["commands"]
    if not isinstance(commands, list) or not commands:
        raise ValueError("receipt must contain every local command")
    expected_commands = json.loads(MANIFEST.read_text())["commands"]
    if len(commands) != len(expected_commands):
        raise ValueError("receipt must contain every manifest command exactly once")
    for command, expected in zip(commands, expected_commands):
        if set(command) != {"name", "command_sha256", "log_sha256", "exit_code"}:
            raise ValueError("receipt command fields do not match the local receipt contract")
        if not isinstance(command["name"], str) or not isinstance(command["command_sha256"], str) or not isinstance(command["log_sha256"], str):
            raise ValueError("receipt command hashes are invalid")
        if any(len(command[key]) != 64 or any(ch not in "0123456789abcdef" for ch in command[key])
               for key in ("command_sha256", "log_sha256")):
            raise ValueError("receipt command hashes must be SHA256")
        if type(command["exit_code"]) is not int or command["exit_code"] != 0:
            raise ValueError("receipt contains an unsuccessful local command")
        if command["name"] != expected["name"] or command["command_sha256"] != hashlib.sha256(expected["command"].encode()).hexdigest():
            raise ValueError("receipt command does not match the local test manifest")


def run(output: Path, *, full: bool = False) -> int:
    reject_hosted_runner()
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    if not clean_tree():
        raise SystemExit("local hospital tests require a clean tree before execution")
    require_frontend_dependencies()
    source_sha_start = git("rev-parse", "HEAD")
    definition_start = digest(MANIFEST)
    manifest = json.loads(MANIFEST.read_text())
    commands = manifest.get("commands")
    if manifest.get("version") != 1 or not isinstance(commands, list) or not commands:
        raise SystemExit("invalid local hospital test manifest")
    output.mkdir(parents=True, exist_ok=False)
    os.environ["SAWA_LOCAL_RUN_ID"] = str(time.time_ns())
    os.environ["SAWA_LOCAL_EVIDENCE_DIR"] = str(output.resolve())
    environment = dict(os.environ)
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        environment.pop(key, None)
    branch = git("branch", "--show-current")
    environment["SAWA_LOCAL_TEST_MODE"] = "full" if full or not branch or branch.startswith("release/prod-") else "focused"
    environment["SAWA_IMPACT_FULL"] = "1" if environment["SAWA_LOCAL_TEST_MODE"] == "full" else "0"
    records = []
    source_stable = True
    for index, item in enumerate(commands):
        name, command = item.get("name"), item.get("command")
        if not isinstance(name, str) or not isinstance(command, str):
            raise SystemExit("invalid local hospital test command")
        log = output / f"{index:02d}-{name}.log"
        with log.open("wb") as stream:
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", command], cwd=ROOT,
                                    env=environment, stdout=stream, stderr=subprocess.STDOUT)
        records.append({"name": name, "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                        "log_sha256": digest(log), "exit_code": result.returncode})
        if (git("rev-parse", "HEAD") != source_sha_start
                or digest(MANIFEST) != definition_start or not clean_tree()):
            source_stable = False
            break
    source_sha_end = git("rev-parse", "HEAD")
    receipt = {"receipt_version": 1, "owner_attestation": "manual", "source_sha_start": source_sha_start,
               "source_sha_end": source_sha_end,
               "clean_before": True, "clean_after": clean_tree(), "definition_sha256": digest(MANIFEST),
               "commands": records}
    if (not source_stable or source_sha_start != source_sha_end or definition_start != digest(MANIFEST)
            or not receipt["clean_after"] or len(records) != len(commands)
            or any(record["exit_code"] != 0 for record in records)):
        (output / "failed-run.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return 1
    validate_receipt(receipt, source_sha_start, receipt["definition_sha256"])
    if any(item["name"] == "backend-tests" for item in commands):
        report_path = output / "backend-tests" / "selection.json"
        report = json.loads(report_path.read_text())
        if report["source_sha"] != source_sha_start or report["exitstatus"] != 0:
            raise ValueError("backend selection does not match successful source")
        planned, executed, completed = (report.get(key) for key in ('selected', 'executed', 'completed'))
        if (any(not isinstance(nodes, list) or any(not isinstance(node, str) for node in nodes)
                or len(nodes) != len(set(nodes)) for nodes in (planned, executed, completed))
                or set(planned) != set(executed) or set(planned) != set(completed)
                or report.get('failed') or report.get('not_started') or report.get('incomplete')):
            raise ValueError('backend selection contains unfinished test cases')
        receipt["receipt_version"] = 2
        receipt["backend_selection"] = {"source_sha": source_sha_start,
            "mode": environment["SAWA_LOCAL_TEST_MODE"], "executed": len(executed),
            "reused": len(report["reused"]), "report_sha256": digest(report_path)}
        validate_receipt(receipt, source_sha_start, receipt["definition_sha256"])
    (output / "receipt.json").write_text(json.dumps(sign(receipt), indent=2) + "\n")
    return 0 if receipt["clean_after"] and len(records) == len(commands) and all(x["exit_code"] == 0 for x in records) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-definition-sha256")
    parser.add_argument("--full", action="store_true", help="Execute every backend case instead of dependency-tracked selection")
    parser.add_argument("--require-full", action="store_true")
    args = parser.parse_args()
    if args.validate:
        if not args.expected_source_sha or not args.expected_definition_sha256:
            parser.error("--validate requires expected source and definition hashes")
        validate_receipt(verify_signed(json.loads(args.validate.read_text())), args.expected_source_sha, args.expected_definition_sha256,
                         require_full=args.require_full)
        return 0
    if args.output is None:
        parser.error("--output is required when creating a receipt")
    return run(args.output, full=args.full)


if __name__ == "__main__":
    raise SystemExit(main())
