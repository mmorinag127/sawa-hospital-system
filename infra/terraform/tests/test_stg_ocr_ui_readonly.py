"""Execute read-only workflow source/version gates with isolated synthetic inputs."""
from email.message import Message
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ".github/workflows/verify-stg-ocr-ui.yml"
EXCLUSIONS = [WORKFLOW, "infra/terraform/tests/test_stg_ocr_ui_readonly.py",
              "docs/workflow-demand-read-20261020.md"]


class ReadonlyOCRUI(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / WORKFLOW).read_text(), Loader=yaml.BaseLoader)
        self.job = self.workflow["jobs"]["verify"]
        self.steps = self.job["steps"]
        self.gate = next(step["run"] for step in self.steps if step.get("name", "").startswith("Validate owner"))
        wrapper = next(step for step in self.steps if step.get("name", "").startswith("Verify current web"))
        self.assertEqual(wrapper["shell"], "python")
        self.wrapper = wrapper["run"]

    def test_dispatch_permissions_auth_and_existing_verifiers(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"expected_source_sha", "artifact_run_id", "review_order_id"})
        self.assertTrue(all(item["required"] == "true" and item["type"] == "string" for item in inputs.values()))
        self.assertEqual(self.job["environment"], "staging")
        self.assertEqual(self.job["permissions"], {"contents": "read", "actions": "read", "id-token": "write"})
        for clause in ("github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/develop'",
                       "github.actor == github.repository_owner", "github.triggering_actor == github.repository_owner",
                       "github.repository == 'mmorinag127/sawa-hospital-system'"):
            self.assertIn(clause, self.job["if"])
        self.assertEqual(self.workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertNotEqual(self.workflow["concurrency"]["group"], "deploy-stg")
        auth = next(step for step in self.steps if step.get("id") == "auth")
        deploy = yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)
        original = next(step for step in deploy["jobs"]["deploy-frontend"]["steps"]
                        if step.get("id") == "auth-frontend")
        for field in ("workload_identity_provider", "service_account", "token_format",
                      "id_token_audience", "id_token_include_email"):
            self.assertEqual(auth["with"][field], original["with"][field])
        self.assertEqual(self.job["env"]["GOOGLE_OAUTH_CLIENT_ID"], "${{ vars.STG_GOOGLE_OAUTH_CLIENT_ID }}")
        provenance = next(step for step in self.steps if step.get("name", "").startswith("Validate existing"))
        self.assertEqual(provenance["run"], 'python3 scripts/verify_existing_ocr_artifact.py "$OCR_EXISTING_RUN"')
        download = next(step for step in self.steps if step.get("uses") == "actions/download-artifact@v4")
        self.assertLess(self.steps.index(provenance), self.steps.index(download))
        self.assertLess(self.steps.index(download), self.steps.index(auth))
        self.assertEqual(download["with"]["name"], "live-ocr-stg")
        self.assertEqual(download["with"]["repository"], "mmorinag127/sawa-hospital-system")
        self.assertEqual(download["with"]["run-id"], "${{ inputs.artifact_run_id }}")
        text = (ROOT / WORKFLOW).read_text()
        for forbidden in ("gcloud ", "docker ", "terraform ", "npm run build", "continue-on-error", "workflow_call", "<<"):
            self.assertNotIn(forbidden, text)
        for script in ("verify-ocr-live.mjs", "verify-recognition-review-live.mjs"):
            self.assertIn(script, self.wrapper)
        self.assertEqual(self.steps[-1]["if"], "always()")
        for path in EXCLUSIONS:
            self.assertFalse(any(fnmatch(path, pattern) for pattern in deploy["on"]["push"]["paths"]))
        for step in self.steps:
            if "run" in step:
                if step.get("shell") == "python":
                    compile(step["run"], WORKFLOW, "exec")
                else:
                    self.assertEqual(subprocess.run(["bash", "-n"], input=step["run"], text=True,
                                                   capture_output=True).returncode, 0)

    def test_actual_git_gate_rejects_every_nonexcluded_change_and_foreign_context(self):
        with TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=directory, text=True,
                                               stderr=subprocess.DEVNULL).strip()
            git("init")
            git("config", "user.name", "Synthetic")
            git("config", "user.email", "synthetic@example.invalid")
            (Path(directory) / "sentinel").write_text("base")
            git("add", ".")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            env = {**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/develop",
                   "GITHUB_REPOSITORY": "mmorinag127/sawa-hospital-system", "REPOSITORY_OWNER": "mmorinag127",
                   "ACTOR": "mmorinag127", "TRIGGERING_ACTOR": "mmorinag127", "EXPECTED_SOURCE_SHA": base,
                   "GITHUB_SHA": base, "OCR_EXISTING_RUN": "123", "REVIEW_ORDER_ID": "ORD12345678",
                   "GOOGLE_OAUTH_CLIENT_ID": "123-synthetic.apps.googleusercontent.com"}
            def gate(overrides=None):
                return subprocess.run(["bash", "-c", self.gate], cwd=directory,
                                      env={**env, **(overrides or {})}, capture_output=True).returncode
            self.assertEqual(gate(), 0)
            for key, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/main"),
                               ("GITHUB_REPOSITORY", "foreign/repo"), ("REPOSITORY_OWNER", "foreign"),
                               ("ACTOR", "foreign"), ("TRIGGERING_ACTOR", "foreign"),
                               ("EXPECTED_SOURCE_SHA", "HEAD; echo unsafe"), ("OCR_EXISTING_RUN", "1;"),
                               ("REVIEW_ORDER_ID", "ORD12345678?query"),
                               ("GOOGLE_OAUTH_CLIENT_ID", "167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com")):
                with self.subTest(key=key):
                    self.assertNotEqual(gate({key: value}), 0)
            for path in EXCLUSIONS:
                target = Path(directory) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("verification only")
            git("add", ".")
            git("commit", "-m", "allowed verification files")
            env["GITHUB_SHA"] = git("rev-parse", "HEAD")
            self.assertEqual(gate(), 0)
            allowed = env["GITHUB_SHA"]
            for path in ("frontend/src/a.tsx", "frontend/public/a", "frontend/package-lock.json",
                         "frontend/Dockerfile", "frontend/next.config.js", "backend/src/a.py", "backend/uv.lock",
                         "ocr_pipeline/a.py", "agent_runtime/a.py", "scripts/verify-ocr-live.mjs",
                         ".github/workflows/deploy-stg.yml", EXCLUSIONS[1] + ".extra", "unlisted"):
                with self.subTest(path=path):
                    git("checkout", "--detach", allowed)
                    target = Path(directory) / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("unapproved difference")
                    git("add", ".")
                    git("commit", "-m", "nonexcluded")
                    env["GITHUB_SHA"] = git("rev-parse", "HEAD")
                    self.assertNotEqual(gate(), 0)

    def execute_wrapper(self, *, versions=None, exits=(0, 0), bad_content=False, crash=False, resolved_sha=None):
        expected, verification = "a" * 40, "b" * 40
        versions = versions or [expected[:12], expected[:12]]
        responses = []
        for version in versions:
            response = SimpleNamespace(status=200, geturl=lambda: "https://web-stg-avlnzjjrca-dt.a.run.app/login")
            response.headers = Message()
            response.headers["Content-Type"] = "application/json" if bad_content else "text/html"
            response.read = lambda limit, version=version: (
                '<footer aria-label="deploy version"><span>version <!-- -->' + version + '</span></footer>').encode()
            class Context:
                def __enter__(self, response=response):
                    return response
                def __exit__(self, *args):
                    pass
            responses.append(Context())
        calls, namespace = [], {}
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if crash:
                raise RuntimeError("PRIVATE crash details")
            # Persist normal and blocked case evidence as the existing verifier would.
            if len(calls) == 1:
                Path("tmp/ocr-live/browser-result.json").write_text(json.dumps(
                    {"cases": [{"status": "passed"}, {"status": "blocked" if exits[0] else "passed"}]}))
            return SimpleNamespace(returncode=exits[len(calls) - 1])
        with TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with patch.dict(os.environ, {"GITHUB_SHA": verification, "EXPECTED_SOURCE_SHA": expected,
                                             "LIVE_ID_TOKEN": "PRIVATE-token"}), \
                        patch("urllib.request.build_opener") as opener, \
                        patch("subprocess.check_output", return_value=(resolved_sha or expected) + "\n"), \
                        patch("subprocess.run", side_effect=run), self.assertRaises(SystemExit) as stopped:
                    opener.return_value.open.side_effect = responses
                    exec(compile(self.wrapper, WORKFLOW, "exec"), namespace)
                report = json.loads(Path("tmp/ocr-live/source-check.json").read_text())
                self.assertNotIn("PRIVATE", json.dumps(report))
                self.assertEqual(report["verification_source_sha"], verification)
                self.assertEqual(report["expected_deployed_source_sha"], expected)
                if calls and not crash:
                    self.assertEqual(json.loads(Path("tmp/ocr-live/browser-result.json").read_text()),
                                     {"cases": [{"status": "passed"}, {"status": "blocked" if exits[0] else "passed"}]})
                for args, kwargs in calls:
                    self.assertEqual(kwargs["env"]["GITHUB_SHA"], expected)
                    self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                for call in opener.return_value.open.call_args_list:
                    self.assertEqual(call.args, ("https://web-stg-avlnzjjrca-dt.a.run.app/login",))
                    self.assertEqual(call.kwargs, {"timeout": 30})
                with self.assertRaises(ValueError):
                    namespace["NoRedirect"]().redirect_request(None)
                return stopped.exception.code, report, calls
            finally:
                os.chdir(previous)

    def test_serving_sha_is_checked_twice_and_distinguished_from_verification_sha(self):
        code, report, calls = self.execute_wrapper()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["before_observed_version_prefix"], "a" * 12)
        self.assertEqual(report["after_observed_version_prefix"], "a" * 12)
        self.assertEqual(report["before_resolved_deployed_source_sha"], "a" * 40)
        self.assertEqual(report["after_resolved_deployed_source_sha"], "a" * 40)
        self.assertEqual([call[0][1] for call in calls],
                         ["frontend/scripts/verify-ocr-live.mjs", "frontend/scripts/verify-recognition-review-live.mjs"])

    def test_blocked_ocr_does_not_skip_review_or_postcheck_and_never_becomes_success(self):
        code, report, calls = self.execute_wrapper(exits=(1, 0))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["after_observed_version_prefix"], "a" * 12)

    def test_version_mismatch_or_unexpected_html_fails_closed(self):
        for options, phase, count in (({"versions": ["c" * 12, "a" * 12]}, "before", 0),
                                     ({"versions": ["a" * 12, "c" * 12]}, "after", 2),
                                     ({"bad_content": True}, "before", 0),
                                     ({"versions": ["unknown", "unknown"]}, "before", 0),
                                     ({"resolved_sha": "c" * 40}, "before", 0),
                                     ({"crash": True}, "ocr", 1)):
            with self.subTest(options=options):
                code, report, calls = self.execute_wrapper(**options)
                self.assertEqual(code, 1)
                self.assertEqual(report["failure_phase"], phase)
                self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()
