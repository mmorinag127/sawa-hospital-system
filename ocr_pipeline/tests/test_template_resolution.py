from app import template_resolution


def test_build_template_resolution_does_not_block_classifier_mismatch_within_requested_scope() -> None:
    resolution = template_resolution.build_template_resolution(
        requested_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        requested_template_ids=[
            "fax_layout_regular_soft_mixer_forbidden_v1",
            "fax_layout_floor_2f3f_v1",
        ],
        resolved_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        classification={
            "matched_template_id": "fax_layout_floor_2f3f_v1",
            "confidence": 0.93,
            "candidates": [
                {"id": "fax_layout_floor_2f3f_v1", "score": 0.93},
                {"id": "fax_layout_regular_soft_mixer_forbidden_v1", "score": 0.91},
            ],
        },
        page_correction_summary={
            "pages": [
                {
                    "mode": "template_warp",
                    "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                }
            ]
        },
    )

    assert resolution["mismatch"] is True
    assert resolution["classifier_mismatch"] is True
    assert resolution["warp_mismatch"] is False
    assert resolution["blocked"] is False
    assert resolution["blocked_reasons"] == []
