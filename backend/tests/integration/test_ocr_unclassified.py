import sys
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.ocr_pipeline_service import run_ocr_pipeline  # noqa: E402


def test_ocr_pipeline_rejects_unowned_pdf_before_classification(monkeypatch, tmp_path):
    pdf_path = tmp_path / "unknown.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\\n%EOF\\n")

    original = pdf_path.read_bytes()
    with pytest.raises(ValueError, match="^selected_pdf_execution_changed$"):
        run_ocr_pipeline(
            pdf_bytes=original,
            job_id="JOB-UNCLASSIFIED",
            facility_id=None,
            input_reference=str(pdf_path),
            preferred_template_id=None,
        )
    assert pdf_path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [pdf_path]
