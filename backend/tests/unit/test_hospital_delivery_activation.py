import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import activate_hospital_delivery as activation


@pytest.mark.parametrize("case", ["success", "wrong-image", "resume-failure"])
def test_activation_requires_verified_images_and_stops_failed_activation(case, monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("PRODUCTION_AGENT_IMAGE", "agent@sha256:456")
    for key in ("PRODUCTION_FRONTEND_IMAGE", "PRODUCTION_BACKEND_IMAGE", "PRODUCTION_PRODUCER_IMAGE"):
        monkeypatch.setenv(key, "expected@sha256:123")
    monkeypatch.setattr(activation, "require_context", lambda _: "prod")
    bootstrap = Mock()
    monkeypatch.setattr(activation, "bootstrap", bootstrap)
    monkeypatch.setitem(sys.modules, "registry_image_digest", SimpleNamespace(resolve_image_digest=lambda image: image))
    states = {}
    mutations = []

    def cloud(*args):
        if args[:3] == ("run", "services", "describe"):
            return {"status": {"traffic": [{"percent": 100, "revisionName": args[3]}]}}
        if args[:3] == ("run", "revisions", "describe"):
            digest = "wrong" if case == "wrong-image" else "expected@sha256:123"
            containers = [{"name": "web", "image": digest}]
            if args[3] == "web-prod":
                containers.append({"name": "agent-runtime", "image": "agent@sha256:456"})
            return {"spec": {"containers": containers}, "status": {
                "containerStatuses": [{"name": row["name"], "imageDigest": row["image"]} for row in containers],
                "conditions": [{"type": "Ready", "status": "True"}]}}
        group, _, command, name, *_ = args
        if command == "describe":
            return {"state": states.get(name, "PAUSED")}
        mutations.append((command, name))
        if command == "resume":
            if case == "resume-failure" and group == "scheduler":
                raise RuntimeError("denied")
            states[name] = "RUNNING" if group == "tasks" else "ENABLED"
        elif command == "pause":
            states[name] = "PAUSED"
        else:
            raise AssertionError(args)

    monkeypatch.setattr(activation, "gcloud", cloud)
    if case == "success":
        activation.run()
        assert len(mutations) == 4
        assert all(command == "resume" for command, _ in mutations)
        assert bootstrap.call_args_list[-1].kwargs == {}
    else:
        with pytest.raises(RuntimeError):
            activation.run()
        if case == "wrong-image":
            assert not mutations
        else:
            assert states and set(states.values()) == {"PAUSED"}
            assert (tmp_path / "hospital-activation-failure.json").exists()
