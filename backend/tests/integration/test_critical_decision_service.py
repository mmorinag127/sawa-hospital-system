import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import critical_decision_service, order_service  # noqa: E402


def test_sync_pending_decisions_adds_and_prunes_entries() -> None:
    order_service.clear_all()

    first = critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-1",
        [
            {
                "decision_type": "facility",
                "title": "施設候補を選択",
                "candidates": [{"value": "FAC00001", "label": "施設A"}],
                "blocked_reasons": ["facility_choice_required"],
            },
            {
                "decision_type": "week",
                "title": "対象週を選択",
                "candidates": [{"value": "2026-03@2026-03-22~2026-03-28", "label": "2026-03"}],
                "blocked_reasons": ["week_choice_required"],
            },
        ],
    )

    assert {item["decision_type"] for item in first} == {"facility", "week"}

    second = critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-1",
        [
            {
                "decision_type": "facility",
                "title": "施設候補を選択",
                "candidates": [
                    {"value": "FAC00001", "label": "施設A"},
                    {"value": "FAC00002", "label": "施設B"},
                ],
                "blocked_reasons": ["facility_choice_required"],
            }
        ],
    )

    assert {item["decision_type"] for item in second} == {"facility"}
    facility = second[0]
    assert len((facility.get("candidate_set_json") or {}).get("candidates") or []) == 2


def test_choose_decision_marks_value_and_preserves_latest_entry() -> None:
    order_service.clear_all()

    critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-2",
        [
            {
                "decision_type": "facility",
                "title": "施設候補を選択",
                "candidates": [
                    {"value": "FAC00001", "label": "施設A"},
                    {"value": "FAC00002", "label": "施設B"},
                ],
                "blocked_reasons": ["facility_choice_required"],
            }
        ],
    )

    chosen = critical_decision_service.choose_decision(
        "ORD-CRIT-2",
        "facility",
        "FAC00002",
        selected_by="operator",
    )

    assert isinstance(chosen, dict)
    assert chosen["decision_type"] == "facility"
    assert chosen["selected_value"] == "FAC00002"
    assert chosen["selected_by"] == "operator"

    listed = critical_decision_service.list_decisions("ORD-CRIT-2")
    assert len(listed) == 1
    assert listed[0]["selected_value"] == "FAC00002"


def test_sync_pending_decisions_preserves_selected_value_when_refreshing_candidates() -> None:
    order_service.clear_all()

    critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-3",
        [
            {
                "decision_type": "template",
                "title": "票面テンプレートを選択",
                "candidates": [{"value": "layout-a", "label": "layout-a"}],
                "blocked_reasons": ["template_choice_required"],
            }
        ],
    )
    critical_decision_service.choose_decision("ORD-CRIT-3", "template", "layout-a", selected_by="operator")

    refreshed = critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-3",
        [
            {
                "decision_type": "template",
                "title": "票面テンプレートを選択",
                "candidates": [
                    {"value": "layout-a", "label": "layout-a"},
                    {"value": "layout-b", "label": "layout-b"},
                ],
                "blocked_reasons": ["template_choice_required"],
            }
        ],
    )

    assert len(refreshed) == 1
    assert refreshed[0]["selected_value"] == "layout-a"
    assert len((refreshed[0].get("candidate_set_json") or {}).get("candidates") or []) == 2


def test_sync_pending_decisions_preserves_structured_choice_metadata() -> None:
    order_service.clear_all()

    synced = critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-4",
        [
            {
                "decision_type": "quantity",
                "title": "重要な数量候補を選択",
                "ambiguity_scope": "high_impact_quantity",
                "decision_source": "critical_quantity_candidates",
                "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                "candidates": [
                    {"candidate_id": "qty-a", "value": "qty-a", "label": "3を採用"},
                    {"candidate_id": "qty-b", "value": "qty-b", "label": "8を採用"},
                ],
                "blocked_reasons": ["quantity_choice_required"],
            }
        ],
    )

    assert len(synced) == 1
    candidate_set = synced[0]["candidate_set_json"]
    assert candidate_set["ambiguity_scope"] == "high_impact_quantity"
    assert candidate_set["decision_source"] == "critical_quantity_candidates"
    assert candidate_set["evidence_ref"] == {"page": 1, "row_index": 7, "column_index": 4}


def test_sync_pending_decisions_invalidates_selected_value_when_evidence_lineage_changes() -> None:
    order_service.clear_all()

    critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-5",
        [
            {
                "decision_type": "facility",
                "title": "施設候補を選択",
                "base_evidence_run_id": "EVD-OLD",
                "candidates": [{"value": "FAC00001", "label": "施設A"}],
                "blocked_reasons": ["facility_choice_required"],
            }
        ],
        base_evidence_run_id="EVD-OLD",
    )
    chosen = critical_decision_service.choose_decision(
        "ORD-CRIT-5",
        "facility",
        "FAC00001",
        selected_by="operator",
        current_evidence_run_id="EVD-OLD",
    )
    assert isinstance(chosen, dict)
    assert chosen["selected_value"] == "FAC00001"

    refreshed = critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-5",
        [
            {
                "decision_type": "facility",
                "title": "施設候補を選択",
                "base_evidence_run_id": "EVD-NEW",
                "candidates": [{"value": "FAC00002", "label": "施設B"}],
                "blocked_reasons": ["facility_choice_required"],
            }
        ],
        base_evidence_run_id="EVD-NEW",
    )

    assert len(refreshed) == 1
    assert refreshed[0]["base_evidence_run_id"] == "EVD-NEW"
    assert refreshed[0]["selected_value"] is None


def test_choose_decision_returns_none_when_evidence_lineage_is_stale() -> None:
    order_service.clear_all()

    critical_decision_service.sync_pending_decisions(
        "ORD-CRIT-6",
        [
            {
                "decision_type": "week",
                "title": "対象週を選択",
                "base_evidence_run_id": "EVD-CURRENT",
                "candidates": [{"value": "2026-03@2026-03-22~2026-03-28", "label": "2026-03"}],
                "blocked_reasons": ["week_choice_required"],
            }
        ],
        base_evidence_run_id="EVD-CURRENT",
    )

    stale = critical_decision_service.choose_decision(
        "ORD-CRIT-6",
        "week",
        "2026-03@2026-03-22~2026-03-28",
        selected_by="operator",
        current_evidence_run_id="EVD-NEXT",
    )

    assert stale is None
