"""Read-only pixel replay, not live OCR or quantity parity proof."""
import json
import os
from pathlib import Path

import pytest

from src.services import hakodate_step_review_pipeline_service as pipeline


def test_reported_missing_junction_from_preserved_source(monkeypatch, tmp_path):
    root = os.environ.get("OCR_RECOVERY_EVIDENCE_DIR")
    if not root:
        pytest.skip("explicit preserved source evidence directory required")
    root = Path(root)
    evidence = json.loads((root / "ord3-axis-forensic.json").read_text())
    image = pipeline.cv2.imread(str(root / "ord3-raw-rectified.png"))
    assert image is not None
    kwargs = {"corrected_xs": evidence["xs"], "template_ys": evidence["template_ys"]}
    after, _ = pipeline._detect_table_intersections_for_row_axis(image, **kwargs)
    with monkeypatch.context() as old:
        old.setattr(pipeline, "_restore_supported_ruling_junctions", lambda inv, horizontal, vertical, **kw: horizontal)
        before, _ = pipeline._detect_table_intersections_for_row_axis(image, **kwargs)
    def missing_corner(points):
        return [point for point in points if abs(point["x"] - 15) < 10 and abs(point["y"] - 2125.5) < 10]
    assert missing_corner(before) == []
    assert len(missing_corner(after)) == 1
    (tmp_path / "pixel-replay.json").write_text(json.dumps({
        "before_intersections": len(before), "after_intersections": len(after),
        "before_corner": missing_corner(before), "after_corner": missing_corner(after),
        "scope": "preserved image junction only; no live recognition or quantity comparison",
    }, indent=2))
