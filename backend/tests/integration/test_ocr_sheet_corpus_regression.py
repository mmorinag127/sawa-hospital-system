"""Archived data integrity and authored operator-sheet storage contracts.

The source-alignment review identifies a real PDF/label mismatch. That pair is
not executed as OCR accuracy or production-parity proof. The separate saved-sheet
case treats the unchanged 64-row JSON as operator input, uses a synthetic source
and doubles only external recognition/rendering; it is not a corpus recognition
replacement. Retired Yomitoku/LLM row-index-copy algorithms are out of scope.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.services import order_service
from tests.contract.auth_support import registered_hospital_operator
from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures/ocr_sheet_corpus"
MANIFEST = json.loads((FIXTURE_ROOT / "corpus_manifest.json").read_text())
ALIGNMENT = json.loads((FIXTURE_ROOT / "source_alignment_review.json").read_text())


def _json(relative):
    return json.loads((FIXTURE_ROOT / relative).read_text())


def test_corpus_manifest_does_not_trust_the_known_mismatched_pair():
    assert MANIFEST["default_statuses"] == ["trusted"]
    assert MANIFEST["suites"]["yomitoku"]["ORD7499f262"]["status"] == "trusted"
    for suite in ("llm_row_index_copy", "manual_labels"):
        entry = MANIFEST["suites"][suite]["ORDbabf3c73"]
        assert entry["status"] == "source_mismatch"
        assert "source_alignment_review.json" in entry["note"]


def test_archived_pdf_and_labels_remain_unchanged_and_the_period_mismatch_is_explicit():
    review = ALIGNMENT["ORDbabf3c73"]
    assert review["status"] == "source_mismatch" and review["resolved"] is False
    for item in review["files"]:
        assert sha256((FIXTURE_ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert review["pdf_body_period"] == {"start": "2026-02-15", "end": "2026-02-21"}
    assert review["pdf_page_count"] == 1
    assert review["facility_id"] == "FAC00009"
    label = _json(review["manual_label_path"])
    assert label["facility_id"] == review["facility_id"]
    assert label["week_id"] == "2026-02"
    assert len(label["rows"]) == 64
    dates = {date.fromisoformat("2026-" + row[0].replace("/", "-")) for row in label["rows"]}
    assert (min(dates), max(dates)) == (date(2026, 2, 1), date(2026, 2, 8))
    assert dates.isdisjoint({date(2026, 2, day) for day in range(15, 22)})
    provider = _json("llm/ORDbabf3c73.llm_rows.json")
    assert {f"2/{day}" for day in range(15, 22)} <= set(provider["date_strings"])
    assert _json("llm/ORDbabf3c73.base_sheet.json") == label


def test_manual_label_discovery_never_substitutes_a_different_pdf():
    candidates = []
    for path in sorted((FIXTURE_ROOT / "manual_labels").glob("*.expected_sheet.json")):
        identity = path.name.removesuffix(".expected_sheet.json")
        exact = FIXTURE_ROOT / "pdfs" / f"{identity}.pdf"
        if exact.exists():
            candidates.append(identity)
    assert candidates == ["ORDbabf3c73"]
    assert ALIGNMENT[candidates[0]]["status"] == "source_mismatch"


@pytest.fixture
def authored_operator_corpus_case(isolated_formal_schema, tmp_path, monkeypatch):
    """The label is explicitly typed input, never a recognition expectation."""
    label = _json("manual_labels/ORDbabf3c73.expected_sheet.json")
    fields, headers = label["fields"], label["header"]
    pairs = {
        "qty.regular_2f": ("regular", "2F"), "qty.regular_3f": ("regular", "3F"),
        "qty.soft_2f": ("soft", "2F"), "qty.soft_3f": ("soft", "3F"),
        "qty.mixer_2f": ("mixer", "2F"), "qty.mixer_3f": ("mixer", "3F"),
    }
    assert fields == ["date_mmdd", "daypart", "menu", *pairs, "remarks"]
    assert len(headers) == len(fields)
    roles = {"date_mmdd": "date", "daypart": "daypart", "menu": "menu_name", "remarks": "note"}
    columns = []
    for index, field in enumerate(fields):
        column = {"index": index, "source_index": index, "name": field,
                  "header": headers[index], "role": "quantity" if field in pairs else roles[field]}
        if field in pairs:
            column.update(diet_type=pairs[field][0], area_id=pairs[field][1])
        columns.append(column)
    counters, entries = defaultdict(int), []
    for index, row in enumerate(label["rows"]):
        assert len(row) == len(fields)
        day = date.fromisoformat("2026-" + row[0].replace("/", "-"))
        assert date(2026, 2, 1) <= day <= date(2026, 2, 8)
        key = day, row[1]
        entries.append({"id": f"operator-label-row-{index}", "monthly_menu_id": "2026-02",
            "menu_date": day, "daypart": row[1], "name": row[2], "slot_index": counters[key],
            "diet_type": "regular"})
        counters[key] += 1
    with registered_hospital_operator(monkeypatch) as actor:
        case = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            facility="AUTHORED-OPERATOR-LABEL", week="2026-02@2026-02-01~2026-02-08",
            period="2026-02@2026-02-01~2026-02-08", received=datetime(2026, 2, 1, 9),
            columns=columns, lines=[], menu_entries=entries)
        # Deliberately different from operator values; no label-as-OCR-output injection.
        case.source_sheet = case.produce(cell_values="0")["sheet"]
        yield case, label


def test_authored_operator_sheet_preserves_all_64_rows_quantities_and_export_identity(
    authored_operator_corpus_case, tmp_path,
):
    case, label = authored_operator_corpus_case
    sheet = {key: deepcopy(label[key]) for key in ("fields", "header", "rows", "row_ids")}
    assert len(sheet["rows"]) == len(sheet["row_ids"]) == 64
    assert len(set(sheet["row_ids"])) == 64
    original_source = deepcopy(case.source_sheet)
    case.save(sheet)
    saved, error = order_service.get_ocr_sheet(case.id)
    assert error is None and saved is not None
    for key in sheet:
        assert saved[key] == sheet[key]
    exported, error = order_service.export_ocr_sheet_label(case.id, output_dir=tmp_path / "operator-export")
    assert error is None and exported is not None
    for key in sheet:
        assert exported["sheet"][key] == sheet[key]
    output = Path(exported["output_path"])
    assert output.parent == tmp_path / "operator-export"
    assert json.loads(output.read_text()) == exported["sheet"]
    # Saved operator numbers never rewrite selected original OCR cells.
    with case.readonly():
        current_source = case.get("workflow-v2/sheet-source")["sheet"]
    assert current_source == original_source
