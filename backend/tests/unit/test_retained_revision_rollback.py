import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import restore_hospital_traffic as rollback


@pytest.mark.parametrize("case", ["restore", "wrong-uid", "drift", "incompatible", "not-ready", "wrong-traffic"])
def test_retained_revision_protocol(case, monkeypatch, tmp_path):
    target = {"revision": "worker-prod-before", "uid": "original", "images": ["digest"]}
    record = {"run": "17", "sha": "release", "ref": "refs/heads/release/prod-example",
              "project": rollback.PROJECT, "region": rollback.REGION, "services": {"worker-prod": target}}
    (tmp_path / "rollback").mkdir()
    (tmp_path / "rollback/hospital-rollback.json").write_text(json.dumps(record))
    for key, value in {"RUNNER_TEMP": str(tmp_path), "ORIGINAL_RUN": "17", "TARGET_SERVICE": "worker-prod",
                       "GITHUB_REF": record["ref"], "GITHUB_SHA": "release", "CURRENT_REVISION": "after"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(rollback, "require_context", lambda _: "prod")
    monkeypatch.setattr(rollback, "cloud", lambda *args: {
        "metadata": {"uid": "other" if case == "wrong-uid" else "original",
                     "labels": {"serving.knative.dev/service": "worker-prod"}},
        "status": {"conditions": [{"type": "Ready", "status": "False" if case == "not-ready" else "True"}]}})
    monkeypatch.setattr(rollback, "require_rollback_compatibility",
                        Mock(side_effect=RuntimeError() if case == "incompatible" else None))
    monkeypatch.setattr(rollback, "serving", Mock(side_effect=[
        "different" if case == "drift" else "after", "after" if case == "wrong-traffic" else target["revision"]]))
    mutation = Mock()
    monkeypatch.setattr(rollback.subprocess, "run", mutation)
    if case == "restore":
        rollback.main()
    else:
        with pytest.raises(RuntimeError):
            rollback.main()
    if case in {"restore", "wrong-traffic"}:
        mutation.assert_called_once_with(["gcloud", "run", "services", "update-traffic", "worker-prod",
            "--project=" + rollback.PROJECT, "--region=" + rollback.REGION,
            "--to-revisions=worker-prod-before=100", "--quiet"], check=True)
    else:
        mutation.assert_not_called()
