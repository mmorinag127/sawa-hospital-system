import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.fax_roi_extractor import build_ocr_output  # noqa: E402


def test_failed_cells_contract():
    failed = [{"row": "day1", "col": "regular_2f", "reason": "unreadable"}]
    payload = build_ocr_output(
        job_id="JOB-FAILED",
        status="done",
        template_id="TPL-001",
        facility_id="FAC-001",
        input_reference="input.pdf",
        output_reference="output.json",
        quantities={},
        notes="",
        failed_cells=failed,
    )
    assert payload["failed_cells"] == failed
