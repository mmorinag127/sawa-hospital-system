import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import position_column_mapping_service  # noqa: E402


def _position_payload() -> dict:
    return {
        "template_resolution": {
            "resolved_template_id": "fac00004-template",
            "candidate_template_ids": ["fac00004-template"],
            "confidence": 0.92,
            "blocked": False,
            "blocked_reasons": [],
            "decision_source": "position_fallback",
        },
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_x",
            "resolved_column_mapping_id": "4:qty.regular_x",
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "position_fallback",
            "confidence": 0.9,
        },
        "column_mapping_candidates": [
            {
                "candidate_id": "pcm-stale",
                "candidate_type": "position_fallback_candidate",
                "value": "4:qty.regular_x",
                "label": "stale-position-fallback",
                "score": 0.9,
                "decision_source": "position_fallback",
                "auto_selectable": True,
            }
        ],
    }


def test_position_fallback_augment_is_disabled() -> None:
    payload = _position_payload()

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        {"columns": []},
        template_id="fac00004-template",
    )

    assert augmented is payload


def test_position_fallback_is_never_ready_or_partial() -> None:
    payload = _position_payload()

    assert (
        position_column_mapping_service.payload_uses_ready_position_fallback(
            payload,
            template={"columns": []},
        )
        is False
    )
    assert position_column_mapping_service.payload_uses_partial_position_fallback(payload) is False
