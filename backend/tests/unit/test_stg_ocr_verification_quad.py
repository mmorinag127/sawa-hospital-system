import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_stg_ocr_upload.py"
spec = importlib.util.spec_from_file_location("verify_stg_ocr_upload", SCRIPT)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def case():
    return {
        "identity_verified": True,
        "approved_quad": {
            "coordinate_space": {"mode": "render_width", "width": 1864},
            "decision": "approved_estimate",
            "quad_px": [[1, 2], [3, 4], [5, 6], [7, 8]],
        },
    }


def test_verified_quad_uses_operator_save_endpoint(monkeypatch):
    request = Mock(return_value={"saved": True})
    monkeypatch.setattr(verification, "request", request)
    assert verification.apply_verified_quad(case(), "ORDtest") == {"saved": True}
    assert request.call_args.args[0] == "/orders/ORDtest/workflow-v2/quad-review"
    assert request.call_args.kwargs == {"method": "PUT"}
    assert request.call_args.args[2] == "application/json"
    assert json.loads(request.call_args.args[1]) == {
        "decision": "approved_estimate",
        "quad_px": [[1, 2], [3, 4], [5, 6], [7, 8]],
    }


@pytest.mark.parametrize("quad", [None, {}, {"quad_px": []}])
def test_invalid_quad_does_not_send_request(monkeypatch, quad):
    request = Mock()
    monkeypatch.setattr(verification, "request", request)
    value = case()
    value["approved_quad"] = quad
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")
    request.assert_not_called()


@pytest.mark.parametrize("invalid", [float("nan"), "1", True])
def test_invalid_coordinate_rejected(invalid):
    value = case()
    value["approved_quad"]["quad_px"][0][0] = invalid
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")


def test_unverified_identity_rejected():
    value = case()
    value["identity_verified"] = False
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")


def test_no_operator_quad_is_unchanged():
    assert verification.apply_verified_quad({}, "ORDtest") is None
