import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.ocr_pipeline_service import run_ocr_pipeline  # noqa: E402
from src.services import config_service  # noqa: E402


def test_ocr_pipeline_unclassified(monkeypatch, tmp_path):
    pdf_path = tmp_path / "unknown.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\\n%EOF\\n")

    monkeypatch.setattr(config_service, "load_fax_template_registry", lambda: {})
    output = run_ocr_pipeline(
        pdf_bytes=pdf_path.read_bytes(),
        job_id="JOB-UNCLASSIFIED",
        facility_id=None,
        input_reference=str(pdf_path),
        preferred_template_id=None,
    )
    assert output["status"] == "unclassified"
    assert output["template_id"] is None
