import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_local_runtime_audits as audit


class RuntimeAuditContractTest(unittest.TestCase):
    def test_all_six_installed_runtime_environments_are_named(self):
        self.assertEqual({name for _, name, _ in audit.ENVIRONMENTS}, {
            "backend", "producer", "agent-hospital", "agent-gateway", "agent-shift", "agent-school",
        })
        self.assertEqual(len(audit.ENVIRONMENTS), 6)
        self.assertEqual({interpreter for component, _, interpreter in audit.ENVIRONMENTS
                          if component == "AGENT_RUNTIME"}, {
            "/opt/agent/hospital/bin/python", "/opt/agent/gateway/bin/python",
            "/opt/agent/shift/bin/python", "/opt/agent/school-lunch/bin/python",
        })

    def test_wrong_source_refuses_before_inventory_or_audit(self):
        inspected = json.dumps({"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "amd64",
                                "Config": {"Labels": {"sawa.git_sha": "b" * 40}}})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(audit.subprocess, "check_output", return_value=inspected) as capture, \
                patch.object(audit.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "source differs"):
                audit.audit_runtime("example:tag", "backend", "python", "c" * 40, Path(directory))
            self.assertEqual(capture.call_count, 1)
            run.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_pins_actual_image_and_preserves_unsuccessful_audit(self):
        identity, source = "sha256:" + "a" * 64, "b" * 40
        inspected = json.dumps({"Id": identity, "Os": "linux", "Architecture": "amd64",
                                "Config": {"Labels": {"sawa.git_sha": source}}})
        inventory = json.dumps([["example-package", "1.2.3"]])
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(audit.subprocess, "check_output", side_effect=[inspected, inventory]) as capture, \
                patch.object(audit.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(audit.audit_runtime("example:tag", "backend", "python", source, Path(directory)), 1)
            command = capture.call_args_list[1].args[0]
            self.assertIn(identity, command)
            self.assertNotIn("example:tag", command)
            self.assertIn("--read-only", command)
            self.assertEqual((Path(directory) / "backend.requirements.txt").read_text(), "example-package==1.2.3\n")
            arguments = run.call_args.args[0]
            self.assertIn("--no-deps", arguments)
            self.assertIn("--disable-pip", arguments)
            self.assertNotIn("--ignore-vuln", arguments)

    def test_cpu_lookup_is_recorded_and_skipped_or_missing_packages_fail(self):
        identity, source = "sha256:" + "a" * 64, "b" * 40
        inspected = json.dumps({"Id": identity, "Os": "linux", "Architecture": "amd64",
                                "Config": {"Labels": {"sawa.git_sha": source}}})
        inventory = json.dumps([["torch", "2.13.0+cpu"]])
        for dependencies in ([{"name": "torch", "version": "2.13.0", "vulns": []}],
                             [{"name": "torch", "skip_reason": "unavailable"}], []):
            with self.subTest(dependencies=dependencies), tempfile.TemporaryDirectory() as directory:
                def save_report(arguments):
                    Path(arguments[arguments.index("--output") + 1]).write_text(json.dumps({"dependencies": dependencies}))
                    return subprocess.CompletedProcess(arguments, 0)
                with patch.object(audit.subprocess, "check_output", side_effect=[inspected, inventory]), \
                        patch.object(audit.subprocess, "run", side_effect=save_report):
                    if dependencies and "skip_reason" not in dependencies[0]:
                        self.assertEqual(audit.audit_runtime("example:tag", "backend", "python", source, Path(directory)), 0)
                    else:
                        with self.assertRaises(ValueError):
                            audit.audit_runtime("example:tag", "backend", "python", source, Path(directory))
                self.assertEqual(json.loads((Path(directory) / "backend.inventory.json").read_text()), [["torch", "2.13.0+cpu"]])
                self.assertEqual(json.loads((Path(directory) / "backend.source.json").read_text())["audit_version_aliases"],
                                 {"torch": {"installed": "2.13.0+cpu", "advisory_lookup": "2.13.0"}})

    def test_one_affected_runtime_does_not_hide_other_environments(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": "", "SAWA_LOCAL_EVIDENCE_DIR": directory,
                    "SAWA_LOCAL_TEST_IMAGE_BACKEND": "backend", "SAWA_LOCAL_TEST_IMAGE_OCR_PIPELINE": "producer",
                    "SAWA_LOCAL_TEST_IMAGE_AGENT_RUNTIME": "agent"}), \
                patch.object(audit, "git", return_value="b" * 40), \
                patch.object(audit, "audit_runtime", side_effect=[1, 0, 0, 0, 0, 0]) as run:
            self.assertEqual(audit.main(), 1)
            self.assertEqual(run.call_count, 6)


if __name__ == "__main__":
    unittest.main()
