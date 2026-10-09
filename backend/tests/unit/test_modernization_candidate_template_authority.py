"""Candidate components only, not DB adoption, native OCR, UI or E2E proof.

The existing isolated schema supports real week/menu reads; no decision service
is doubled. Getter sentinels prohibit unversioned-root template substitution.
"""

from copy import deepcopy
from datetime import datetime
import json

import pytest

from src.services import candidate_resolution_service as candidates


@pytest.mark.parametrize(
    "resolution,resolved,confidence,blocked,reasons,choice,expected_candidates",
    [
        pytest.param(
            None, None, "unknown", True, ["template_resolution_missing"], False, [],
            id="missing",
        ),
        pytest.param(
            {"candidates": [{"id": "selected-template", "score": 0.72, "reason": "observed"}],
             "confidence": 0.72, "blocked": True, "blocked_reasons": ["template_choice_required"]},
            None, "medium", True, ["template_choice_required"], False,
            [{"value": "selected-template", "label": "selected-template", "score": 0.72, "reason": "observed"}],
            id="blocked-single",
        ),
        pytest.param(
            {"candidates": [{"id": "selected-template", "score": 0.72},
                            {"id": "other-template", "score": 0.71}], "confidence": 0.72},
            None, "medium", True, ["template_choice_required"], True,
            [{"value": "selected-template", "label": "selected-template", "score": 0.72, "reason": None},
             {"value": "other-template", "label": "other-template", "score": 0.71, "reason": None}],
            id="ambiguous-two",
        ),
        pytest.param(
            {"resolved_template_id": "selected-template", "confidence": 0.94,
             "candidates": [{"id": "selected-template", "score": 0.94}]},
            "selected-template", "high", False, [], False,
            [{"value": "selected-template", "label": "selected-template", "score": 0.94, "reason": None}],
            id="explicit-resolved",
        ),
    ],
)
def test_candidate_template_snapshot_is_not_replaced_by_singleton_root(
    isolated_formal_schema, tmp_path, monkeypatch,
    resolution, resolved, confidence, blocked, reasons, choice, expected_candidates,
):
    root = tmp_path / "facilities.json"
    root.write_text(json.dumps({"facilities": [{
        "facility_id": "F", "facility_name": "Component facility",
        "fax_template_id": "different-root-template",
        "fax_template_ids": ["different-root-template"],
    }]}), encoding="utf-8")
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(root))
    original_root = root.read_bytes()

    def forbidden_root_lookup(*args, **kwargs):
        pytest.fail("candidate template decisions must not consult or collapse root templates")

    monkeypatch.setattr(candidates.config_service, "get_facility_config", forbidden_root_lookup)
    monkeypatch.setattr(candidates.config_service, "collapse_equivalent_template_ids", forbidden_root_lookup)
    payload = {"cells": [{"row_index": index, "text": value, "raw_text": value}
                         for index, value in enumerate(["7", "0", "6", "9"])]}
    if resolution is not None:
        payload["template_resolution"] = deepcopy(resolution)
    original_payload = deepcopy(payload)
    snapshot = candidates.build_template_resolution_snapshot(payload)
    expected = {
        "decision_type": "template", "resolved_value": resolved, "resolved_label": resolved,
        "confidence": confidence, "blocked": blocked, "blocked_reasons": reasons,
        "requires_user_choice": choice, "candidates": expected_candidates,
    }
    assert snapshot == expected

    result = candidates.resolve_order_candidates(
        order_id="COMPONENT-ORDER", facility_code="F",
        week_code="2026-03@2026-03-01~2026-03-07",
        received_at=datetime(2026, 3, 1), evidence_payload=payload,
    )

    assert result["resolutions"]["template"] == snapshot == expected
    assert result["resolutions"]["facility"]["resolved_value"] == "F"
    assert result["resolutions"]["week"]["resolved_value"] == "2026-03@2026-03-01~2026-03-07"
    template_choices = [item for item in result["critical_choices"] if item["decision_type"] == "template"]
    assert template_choices == ([{
        "decision_type": "template", "title": "票面テンプレートを選択",
        "candidates": expected_candidates, "blocked_reasons": reasons,
        "decision_source": None, "ambiguity_scope": None, "evidence_ref": None,
    }] if choice else [])
    assert payload == original_payload
    assert [cell["raw_text"] for cell in payload["cells"]] == ["7", "0", "6", "9"]
    assert root.read_bytes() == original_root
