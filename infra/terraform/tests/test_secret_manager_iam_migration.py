from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "infra/terraform/scripts/secret_manager_iam_migration.py"

spec = importlib.util.spec_from_file_location("secret_manager_iam_migration", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def make_phase1_record(environment: str, github_environment: str) -> dict[str, object]:
    return {
        "environment": environment,
        "phase": "phase1",
        "retain_legacy_project_secret_accessor": True,
        "github_environment": github_environment,
        "phase1_apply_run_id": 123456,
        "phase1_apply_run_url": "https://github.com/example/repo/actions/runs/123456",
        "phase1_apply_head_sha": "0123456789abcdef0123456789abcdef01234567",
        "phase1_plan_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "verification_completed": True,
        "verified_at": "2026-07-30T09:10:11Z",
        "verified_by": "operator@example.com",
        "verification_summary": "Secret access, startup, login, and CI checks passed.",
    }


class SecretManagerIamMigrationTests(unittest.TestCase):
    def test_validate_dispatch_phase1_stg_contract(self) -> None:
        contract = module.validate_dispatch(
            environment="stg",
            phase="phase1",
            branch="develop",
            phase1_record_path="",
            repo_root=REPO_ROOT,
        )
        self.assertEqual(contract["env_dir"], "infra/terraform/envs/stg")
        self.assertEqual(contract["github_environment"], "staging")
        self.assertTrue(contract["retain_legacy_project_secret_accessor"])
        self.assertEqual(contract["artifact_name"], "secret-manager-iam-stg-phase1")

    def test_validate_dispatch_rejects_prod_non_release_branch(self) -> None:
        with self.assertRaises(module.ValidationError):
            module.validate_dispatch(
                environment="prod",
                phase="phase1",
                branch="develop",
                phase1_record_path="",
                repo_root=REPO_ROOT,
            )

    def test_validate_dispatch_phase2_requires_tracked_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = pathlib.Path(tmpdir)
            subprocess.run(["git", "init", "-b", "develop"], cwd=repo_root, check=True, capture_output=True)
            record_dir = repo_root / "infra/terraform/migration_records/secret_manager_iam"
            record_dir.mkdir(parents=True, exist_ok=True)
            record_path = record_dir / "stg-phase1-verified.json"
            record_path.write_text(
                json.dumps(make_phase1_record("stg", "staging"), indent=2) + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", str(record_path.relative_to(repo_root))], cwd=repo_root, check=True)

            contract = module.validate_dispatch(
                environment="stg",
                phase="phase2",
                branch="develop",
                phase1_record_path="infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json",
                repo_root=repo_root,
            )

        self.assertFalse(contract["retain_legacy_project_secret_accessor"])
        self.assertEqual(
            contract["phase1_record_path"],
            "infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json",
        )
        self.assertEqual(contract["phase1_apply_run_id"], 123456)

    def test_validate_plan_phase1_allows_secret_and_project_grants(self) -> None:
        plan = {
            "resource_changes": [
                {
                    "address": 'module.cloudrun.google_secret_manager_secret_iam_member.secret_accessor["web:db-password-stg"]',
                    "change": {"actions": ["create"]},
                },
                {
                    "address": 'module.cloudrun.google_project_iam_member.secret_accessor["web"]',
                    "change": {"actions": ["create"]},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            summary = module.validate_plan(path, "phase1")
        self.assertEqual(summary["total_allowed_changes"], 2)
        self.assertEqual(summary["action_counts"]["create"], 2)

    def test_validate_plan_phase1_blocks_unrelated_resource_change(self) -> None:
        plan = {
            "resource_changes": [
                {
                    "address": "module.cloudrun.google_cloud_run_v2_service.service[\"web\"]",
                    "change": {"actions": ["update"]},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(module.ValidationError):
                module.validate_plan(path, "phase1")

    def test_validate_plan_phase2_allows_only_legacy_binding_delete(self) -> None:
        plan = {
            "resource_changes": [
                {
                    "address": 'module.cloudrun.google_project_iam_member.secret_accessor["worker"]',
                    "change": {"actions": ["delete"]},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            summary = module.validate_plan(path, "phase2")
        self.assertEqual(summary["total_allowed_changes"], 1)
        self.assertEqual(summary["action_counts"]["delete"], 1)

    def test_validate_phase1_run_requires_successful_matching_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = pathlib.Path(tmpdir)
            subprocess.run(["git", "init", "-b", "develop"], cwd=repo_root, check=True, capture_output=True)
            record_dir = repo_root / "infra/terraform/migration_records/secret_manager_iam"
            record_dir.mkdir(parents=True, exist_ok=True)
            record_path = record_dir / "stg-phase1-verified.json"
            record_path.write_text(
                json.dumps(make_phase1_record("stg", "staging"), indent=2) + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", str(record_path.relative_to(repo_root))], cwd=repo_root, check=True)
            run_json = {
                "id": 123456,
                "conclusion": "success",
                "event": "workflow_dispatch",
                "head_sha": "0123456789abcdef0123456789abcdef01234567",
                "head_branch": "develop",
                "name": "Secret Manager IAM Migration",
                "path": ".github/workflows/secret-manager-iam-migration.yml",
            }
            run_path = repo_root / "run.json"
            run_path.write_text(json.dumps(run_json), encoding="utf-8")

            summary = module.validate_phase1_run(
                environment="stg",
                phase1_record_path="infra/terraform/migration_records/secret_manager_iam/stg-phase1-verified.json",
                run_json_path=run_path,
                repo_root=repo_root,
            )

        self.assertEqual(summary["phase1_apply_run_id"], 123456)
        self.assertEqual(summary["phase1_head_branch"], "develop")


if __name__ == "__main__":
    unittest.main()
