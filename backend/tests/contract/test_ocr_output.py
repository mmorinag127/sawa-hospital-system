import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.fax_roi_extractor import build_ocr_output  # noqa: E402


def test_ocr_output_contract_shape():
    payload = build_ocr_output(
        job_id="JOB-001",
        status="done",
        template_id="TPL-001",
        facility_id="FAC-001",
        input_reference="input/orders/sample.pdf",
        output_reference="output/orders/sample.json",
        quantities={"row_1": {"col_1": 1, "col_2": None}},
        notes="",
        failed_cells=[{"row": "row_2", "col": "col_1", "reason": "unreadable"}],
    )
    assert payload["job_id"] == "JOB-001"
    assert payload["status"] == "done"
    assert payload["template_id"] == "TPL-001"
    assert payload["facility_id"] == "FAC-001"
    assert isinstance(payload["quantities"], dict)
    assert isinstance(payload["failed_cells"], list)
