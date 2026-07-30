#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


WORKFLOW_PATH = ".github/workflows/secret-manager-iam-migration.yml"
RECORDS_DIR = pathlib.Path("infra/terraform/migration_records/secret_manager_iam")
WORKFLOW_NAME = "Secret Manager IAM Migration"


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class EnvironmentRule:
    name: str
    env_dir: str
    github_environment: str
    exact_branch: str | None = None
    branch_pattern: str | None = None

    def validate_branch(self, branch: str) -> None:
        if self.exact_branch is not None and branch != self.exact_branch:
            raise ValidationError(
                f"{self.name} migration must run from {self.exact_branch}; got {branch}"
            )
        if self.branch_pattern is not None and re.fullmatch(self.branch_pattern, branch) is None:
            raise ValidationError(
                f"{self.name} migration must run from branches matching "
                f"{self.branch_pattern}; got {branch}"
            )


ENVIRONMENT_RULES: dict[str, EnvironmentRule] = {
    "stg": EnvironmentRule(
        name="stg",
        env_dir="infra/terraform/envs/stg",
        github_environment="staging",
        exact_branch="develop",
    ),
    "prod": EnvironmentRule(
        name="prod",
        env_dir="infra/terraform/envs/prod",
        github_environment="production",
        branch_pattern=r"release/prod-.+",
    ),
}

PHASE_TO_RETAIN_LEGACY = {
    "phase1": True,
    "phase2": False,
}

PHASE_ALLOWED_ACTIONS: dict[str, dict[str, set[str]]] = {
    "phase1": {
        r"^module\.cloudrun\.google_secret_manager_secret_iam_member\.secret_accessor\[":
        {"create"},
        r"^module\.cloudrun\.google_project_iam_member\.secret_accessor\[":
        {"create"},
    },
    "phase2": {
        r"^module\.cloudrun\.google_project_iam_member\.secret_accessor\[":
        {"delete"},
    },
}

HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_non_empty_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _write_outputs(output_path: str | None, values: dict[str, Any]) -> None:
    if not output_path:
        return
    output_file = pathlib.Path(output_path)
    with output_file.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = "" if value is None else str(value)
            handle.write(f"{key}={rendered}\n")


def _dump_json(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    pathlib.Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_record_path(
    record_path: str,
    environment: str,
    repo_root: pathlib.Path | None,
    require_exists: bool,
) -> pathlib.Path:
    if not record_path:
        raise ValidationError("phase2 requires a non-empty phase1 record path")
    candidate = pathlib.Path(record_path)
    resolved = candidate if candidate.is_absolute() else (repo_root / candidate if repo_root else candidate)
    expected_name = f"{environment}-phase1-verified.json"
    if resolved.name != expected_name:
        raise ValidationError(
            f"phase1 record for {environment} must be named "
            f"{RECORDS_DIR / expected_name}"
        )
    expected_parent = RECORDS_DIR if repo_root is None else (repo_root / RECORDS_DIR)
    try:
        resolved.relative_to(expected_parent)
    except ValueError as exc:
        raise ValidationError(
            f"phase1 record must live under {RECORDS_DIR}"
        ) from exc
    if require_exists and not resolved.is_file():
        raise ValidationError(f"phase1 record does not exist: {resolved}")
    return resolved


def _assert_git_tracked(repo_root: pathlib.Path, target: pathlib.Path) -> None:
    relative = target.relative_to(repo_root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(relative)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationError(f"phase1 record must be tracked by git: {relative}")


def load_phase1_record(
    record_path: str,
    environment: str,
    repo_root: pathlib.Path | None = None,
    require_exists: bool = True,
    require_tracked: bool = False,
) -> tuple[pathlib.Path, dict[str, Any]]:
    resolved = _resolve_record_path(
        record_path=record_path,
        environment=environment,
        repo_root=repo_root,
        require_exists=require_exists,
    )
    if require_tracked:
        if repo_root is None:
            raise ValidationError("repo_root is required when require_tracked=true")
        _assert_git_tracked(repo_root, resolved)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("environment") != environment:
        raise ValidationError(
            f"phase1 record environment must be {environment}; got {payload.get('environment')}"
        )
    if payload.get("phase") != "phase1":
        raise ValidationError("phase1 record must declare phase=phase1")
    if payload.get("retain_legacy_project_secret_accessor") is not True:
        raise ValidationError(
            "phase1 record must declare retain_legacy_project_secret_accessor=true"
        )
    if payload.get("github_environment") != ENVIRONMENT_RULES[environment].github_environment:
        raise ValidationError(
            "phase1 record github_environment does not match the target environment"
        )
    phase1_apply_run_id = payload.get("phase1_apply_run_id")
    if not isinstance(phase1_apply_run_id, int) or phase1_apply_run_id <= 0:
        raise ValidationError("phase1_apply_run_id must be a positive integer")
    phase1_head_sha = _require_non_empty_string(payload, "phase1_apply_head_sha")
    if HEX_40_RE.fullmatch(phase1_head_sha) is None:
        raise ValidationError("phase1_apply_head_sha must be a 40-character lowercase git SHA")
    phase1_plan_sha256 = _require_non_empty_string(payload, "phase1_plan_sha256")
    if HEX_64_RE.fullmatch(phase1_plan_sha256) is None:
        raise ValidationError("phase1_plan_sha256 must be a 64-character lowercase SHA256")
    phase1_run_url = _require_non_empty_string(payload, "phase1_apply_run_url")
    if not phase1_run_url.endswith(f"/actions/runs/{phase1_apply_run_id}"):
        raise ValidationError("phase1_apply_run_url must end with the declared run id")
    if payload.get("verification_completed") is not True:
        raise ValidationError("verification_completed must be true before phase2 is allowed")
    _require_non_empty_string(payload, "verified_at")
    _require_non_empty_string(payload, "verified_by")
    _require_non_empty_string(payload, "verification_summary")
    return resolved, payload


def validate_dispatch(
    environment: str,
    phase: str,
    branch: str,
    phase1_record_path: str | None,
    repo_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    if environment not in ENVIRONMENT_RULES:
        raise ValidationError(f"unsupported environment: {environment}")
    if phase not in PHASE_TO_RETAIN_LEGACY:
        raise ValidationError(f"unsupported phase: {phase}")
    rule = ENVIRONMENT_RULES[environment]
    rule.validate_branch(branch)
    retain_legacy = PHASE_TO_RETAIN_LEGACY[phase]
    contract: dict[str, Any] = {
        "environment": environment,
        "phase": phase,
        "branch": branch,
        "env_dir": rule.env_dir,
        "github_environment": rule.github_environment,
        "retain_legacy_project_secret_accessor": retain_legacy,
        "artifact_name": f"secret-manager-iam-{environment}-{phase}",
        "phase1_record_path": "",
    }
    if phase == "phase1":
        if phase1_record_path and phase1_record_path.strip():
            raise ValidationError("phase1 must not receive phase1_record_path")
        return contract
    if repo_root is None:
        raise ValidationError("repo_root is required for phase2 validation")
    resolved, payload = load_phase1_record(
        record_path=phase1_record_path or "",
        environment=environment,
        repo_root=repo_root,
        require_exists=True,
        require_tracked=True,
    )
    contract["phase1_record_path"] = str(resolved.relative_to(repo_root))
    contract["phase1_apply_run_id"] = payload["phase1_apply_run_id"]
    contract["phase1_apply_head_sha"] = payload["phase1_apply_head_sha"]
    contract["phase1_plan_sha256"] = payload["phase1_plan_sha256"]
    return contract


def write_override(output_path: pathlib.Path, retain_legacy: bool) -> None:
    payload = {
        "retain_legacy_project_secret_accessor": retain_legacy,
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_plan(plan_json_path: pathlib.Path, phase: str) -> dict[str, Any]:
    if phase not in PHASE_ALLOWED_ACTIONS:
        raise ValidationError(f"unsupported phase for plan validation: {phase}")
    payload = json.loads(plan_json_path.read_text(encoding="utf-8"))
    allowed_action_map = {
        re.compile(pattern): actions for pattern, actions in PHASE_ALLOWED_ACTIONS[phase].items()
    }
    allowed_changes: list[dict[str, str]] = []
    blocked_changes: list[dict[str, str]] = []
    action_counts: dict[str, int] = {}
    for resource_change in payload.get("resource_changes", []):
        actions = resource_change.get("change", {}).get("actions", [])
        if not isinstance(actions, list):
            raise ValidationError("plan JSON change.actions must be a list")
        meaningful_actions = [action for action in actions if action != "no-op"]
        if not meaningful_actions:
            continue
        address = resource_change.get("address", "<unknown>")
        if len(meaningful_actions) != 1:
            blocked_changes.append(
                {
                    "address": address,
                    "actions": ",".join(meaningful_actions),
                    "reason": "replace/update combinations are not allowed",
                }
            )
            continue
        action = meaningful_actions[0]
        action_counts[action] = action_counts.get(action, 0) + 1
        matched = False
        for pattern, allowed_actions in allowed_action_map.items():
            if pattern.match(address):
                matched = True
                if action in allowed_actions:
                    allowed_changes.append(
                        {
                            "address": address,
                            "action": action,
                        }
                    )
                else:
                    blocked_changes.append(
                        {
                            "address": address,
                            "actions": action,
                            "reason": f"{phase} only allows {sorted(allowed_actions)} for this address",
                        }
                    )
                break
        if not matched:
            blocked_changes.append(
                {
                    "address": address,
                    "actions": action,
                    "reason": "address is outside the migration allowlist",
                }
            )
    if blocked_changes:
        first = blocked_changes[0]
        raise ValidationError(
            "plan contains forbidden resource changes; "
            f"first blocked change: {first['address']} [{first['actions']}] {first['reason']}"
        )
    return {
        "phase": phase,
        "allowed_changes": allowed_changes,
        "action_counts": action_counts,
        "total_allowed_changes": len(allowed_changes),
    }


def validate_phase1_run(
    environment: str,
    phase1_record_path: str,
    run_json_path: pathlib.Path,
    repo_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    _, record = load_phase1_record(
        record_path=phase1_record_path,
        environment=environment,
        repo_root=repo_root,
        require_exists=True,
        require_tracked=repo_root is not None,
    )
    run_payload = json.loads(run_json_path.read_text(encoding="utf-8"))
    run_id = run_payload.get("id")
    if run_id != record["phase1_apply_run_id"]:
        raise ValidationError("GitHub Actions run id does not match the recorded phase1_apply_run_id")
    if run_payload.get("conclusion") != "success":
        raise ValidationError("recorded phase1 GitHub Actions run did not conclude with success")
    if run_payload.get("event") != "workflow_dispatch":
        raise ValidationError("recorded phase1 GitHub Actions run must be workflow_dispatch")
    if run_payload.get("head_sha") != record["phase1_apply_head_sha"]:
        raise ValidationError("recorded phase1 GitHub Actions run head_sha does not match the record")
    if run_payload.get("name") != WORKFLOW_NAME:
        raise ValidationError("recorded phase1 GitHub Actions run is not the expected workflow")
    head_branch = run_payload.get("head_branch")
    if not isinstance(head_branch, str):
        raise ValidationError("recorded phase1 GitHub Actions run head_branch is missing")
    ENVIRONMENT_RULES[environment].validate_branch(head_branch)
    workflow_path = run_payload.get("path")
    if workflow_path is not None and workflow_path != WORKFLOW_PATH:
        raise ValidationError(
            f"recorded phase1 GitHub Actions run path must be {WORKFLOW_PATH}; got {workflow_path}"
        )
    return {
        "phase1_apply_run_id": run_id,
        "phase1_apply_head_sha": run_payload["head_sha"],
        "phase1_head_branch": head_branch,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Secret Manager IAM migration workflow contracts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_dispatch_parser = subparsers.add_parser("validate-dispatch")
    validate_dispatch_parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENT_RULES))
    validate_dispatch_parser.add_argument("--phase", required=True, choices=sorted(PHASE_TO_RETAIN_LEGACY))
    validate_dispatch_parser.add_argument("--branch", required=True)
    validate_dispatch_parser.add_argument("--phase1-record-path", default="")
    validate_dispatch_parser.add_argument("--repo-root")
    validate_dispatch_parser.add_argument("--github-output")
    validate_dispatch_parser.add_argument("--contract-json")

    write_override_parser = subparsers.add_parser("write-override")
    write_override_parser.add_argument("--output", required=True)
    write_override_parser.add_argument("--retain-legacy", required=True, choices=("true", "false"))

    validate_plan_parser = subparsers.add_parser("validate-plan")
    validate_plan_parser.add_argument("--phase", required=True, choices=sorted(PHASE_ALLOWED_ACTIONS))
    validate_plan_parser.add_argument("--plan-json", required=True)
    validate_plan_parser.add_argument("--summary-json")
    validate_plan_parser.add_argument("--github-output")

    validate_phase1_run_parser = subparsers.add_parser("validate-phase1-run")
    validate_phase1_run_parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENT_RULES))
    validate_phase1_run_parser.add_argument("--phase1-record-path", required=True)
    validate_phase1_run_parser.add_argument("--run-json", required=True)
    validate_phase1_run_parser.add_argument("--repo-root")
    validate_phase1_run_parser.add_argument("--summary-json")
    validate_phase1_run_parser.add_argument("--github-output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate-dispatch":
            repo_root = pathlib.Path(args.repo_root).resolve() if args.repo_root else None
            contract = validate_dispatch(
                environment=args.environment,
                phase=args.phase,
                branch=args.branch,
                phase1_record_path=args.phase1_record_path,
                repo_root=repo_root,
            )
            _dump_json(args.contract_json, contract)
            _write_outputs(args.github_output, contract)
            print(json.dumps(contract, indent=2, sort_keys=True))
            return 0
        if args.command == "write-override":
            write_override(
                output_path=pathlib.Path(args.output),
                retain_legacy=args.retain_legacy == "true",
            )
            return 0
        if args.command == "validate-plan":
            summary = validate_plan(
                plan_json_path=pathlib.Path(args.plan_json),
                phase=args.phase,
            )
            _dump_json(args.summary_json, summary)
            _write_outputs(
                args.github_output,
                {
                    "total_allowed_changes": summary["total_allowed_changes"],
                },
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        if args.command == "validate-phase1-run":
            repo_root = pathlib.Path(args.repo_root).resolve() if args.repo_root else None
            summary = validate_phase1_run(
                environment=args.environment,
                phase1_record_path=args.phase1_record_path,
                run_json_path=pathlib.Path(args.run_json),
                repo_root=repo_root,
            )
            _dump_json(args.summary_json, summary)
            _write_outputs(args.github_output, summary)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        raise AssertionError(f"unhandled command: {args.command}")
    except ValidationError as exc:
        print(f"validation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
