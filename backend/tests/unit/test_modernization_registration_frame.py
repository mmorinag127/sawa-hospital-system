import pytest

from test_modernization_dynamic_form_layout import copied_master, entries_for, facility, WEEK
from src.services import hakodate_fixed_quad_registration_service as registration
from src.services import master_order_form_template_service as master


@pytest.mark.parametrize("days,sizes", [
    (range(7), (2, 3, 3)), (range(4), (2, 3, 3)),
    (range(4, 7), (2, 3, 3)), (range(7), (3, 4, 3)),
])
def test_dynamic_ocr_axes_keep_master_frame_and_render_geometry(tmp_path, copied_master, days, sizes):
    entries = entries_for(days, sizes)
    path = master.build_facility_template_xlsx(
        facility_config=facility(), master_template_path=copied_master,
        output_path=tmp_path / "dynamic.xlsx", week_value=WEEK, week_menu_entries=entries,
    )
    canvas = {"canvas_width": 2362, "canvas_height": 4273}
    raw = registration.canonical_template_axes_from_workbook(
        path, sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME, **canvas,
    )
    xs, ys = registration.registration_template_axes_from_workbook(
        path, sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME, **canvas,
    )
    _, frame = registration.canonical_template_axes_from_workbook(
        copied_master, sheet_name=master.MASTER_SHEET_NAME, **canvas,
    )
    assert xs == raw[0]
    assert len(ys) == len(entries) + len(master.TEMPLATE_HEADER_EDGE_ROWS) + 1
    assert (ys[0], ys[-1]) == (frame[0], frame[-1])
    assert all(b > a for a, b in zip(ys, ys[1:]))
    assert [(y - ys[0]) / (ys[-1] - ys[0]) for y in ys] == pytest.approx(
        [(y - raw[1][0]) / (raw[1][-1] - raw[1][0]) for y in raw[1]], abs=1 / (ys[-1] - ys[0]),
    )
    assert registration.canonical_template_axes_from_workbook(
        path, sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME, **canvas,
    ) == raw


def test_ocr_frame_requires_the_locked_master(tmp_path, copied_master, monkeypatch):
    monkeypatch.setattr(master, "MASTER_TEMPLATE_PATH", tmp_path / "missing.xlsx")
    with pytest.raises(FileNotFoundError):
        registration.registration_template_axes_from_workbook(
            copied_master, sheet_name=master.MASTER_SHEET_NAME, canvas_width=2362, canvas_height=4273,
        )
