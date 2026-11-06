import importlib
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
rehearsal = importlib.import_module("rehearse_stg_traffic")


@pytest.mark.parametrize("failure", [False, True])
def test_rehearsal_restores_after_probe(failure, monkeypatch, tmp_path):
    for k, v in {"EXPECTED_REVISION":"worker-stg-current", "RETAINED_REVISION":"worker-stg-old",
                 "RUNNER_TEMP":str(tmp_path), "REHEARSAL_CONFIRMATION":"REHEARSE_STG_TRAFFIC_AND_RESTORE"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rehearsal, "require_context", lambda _: "stg")
    state = {"revision":"worker-stg-current"}
    monkeypatch.setattr(rehearsal, "current", lambda: (state["revision"], "https://staging.example"))
    monkeypatch.setattr(rehearsal, "cloud", lambda *args: {})
    monkeypatch.setattr(rehearsal, "configuration", lambda _: {})
    monkeypatch.setattr(rehearsal.signal, "signal", Mock())
    route = Mock(side_effect=lambda name: state.update(revision=name))
    monkeypatch.setattr(rehearsal, "route", route)
    monkeypatch.setattr(rehearsal, "health", Mock(side_effect=[None, RuntimeError("probe") if failure else None, None]))
    if failure:
        with pytest.raises(RuntimeError, match="probe"):
            rehearsal.main()
    else:
        rehearsal.main()
    assert [c.args[0] for c in route.call_args_list] == ["worker-stg-old", "worker-stg-current"]
    assert state["revision"] == "worker-stg-current"


def test_production_rejected(monkeypatch):
    monkeypatch.setattr(rehearsal, "require_context", lambda _: "prod")
    cloud = Mock()
    monkeypatch.setattr(rehearsal, "cloud", cloud)
    with pytest.raises(RuntimeError):
        rehearsal.main()
    cloud.assert_not_called()


def test_volume_change_is_not_equivalent():
    import copy
    revision = {"metadata": {"labels": {"serving.knative.dev/service": "worker-stg"}},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                "spec": {"containers": [{"image": "old", "volumeMounts": [{"name": "data", "mountPath": "/data"}]}],
                         "volumes": [{"name": "data", "secret": {"secretName": "old-secret"}}]}}
    changed = copy.deepcopy(revision)
    changed["spec"]["containers"][0]["image"] = "new"
    assert rehearsal.configuration(revision) == rehearsal.configuration(changed)
    changed["spec"]["volumes"][0]["secret"]["secretName"] = "other-secret"
    assert rehearsal.configuration(revision) != rehearsal.configuration(changed)


def test_cleanup_refuses_unrelated_traffic(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(rehearsal, "require_context", lambda _: "stg")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("EXPECTED_REVISION", "worker-stg-current")
    monkeypatch.setenv("RETAINED_REVISION", "worker-stg-old")
    (tmp_path / "stg-traffic-rehearsal.json").write_text(json.dumps({"original": "worker-stg-current", "retained": "worker-stg-old"}))
    monkeypatch.setattr(rehearsal, "current", lambda: ("worker-stg-unrelated", "https://example.invalid"))
    route = Mock()
    monkeypatch.setattr(rehearsal, "route", route)
    with pytest.raises(RuntimeError, match="independently"):
        rehearsal.restore()
    route.assert_not_called()
