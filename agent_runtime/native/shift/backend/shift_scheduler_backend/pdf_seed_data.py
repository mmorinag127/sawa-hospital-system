from datetime import date, time, timedelta

from sqlalchemy.orm import Session
from .editor_revision_service import dev_seed_write_snapshot

from .models import (
    Availability,
    Demand,
    Position,
    PositionPriority,
    ReferenceAssignment,
    ReferenceStaffStatus,
    RotationPolicy,
    RotationQueueState,
    RuleConfig,
    ShiftTemplate,
    Skill,
    Staff,
    StaffPairRule,
    StaffPositionRule,
    StaffSkill,
    WorkdayRule,
)
from .pdf_reference_fixtures import add_pdf_source_derived_schedule_exceptions


PDF_STAFF_NAMES = [
    "トム",
    "ザン",
    "レー",
    "ス",
    "ティ",
    "ゾ",
    "トー",
    "キン",
    "ピョー",
    "チー",
    "セイン",
    "ベンス",
    "ペェーン",
    "ウムニー",
    "チャン",
    "リン",
    "ハー",
    "タム",
    "ニー",
    "ガン",
    "チン",
]

SCHOOL_AD_STAFF = {"トム", "ザン", "レー"}
GOHAN_STAFF = {"ゾ", "トー", "ティ"}
PRACTICAL_TRAINEES = {"チャン", "リン", "ハー", "タム", "ニー", "ガン", "チン"}
RETURN_HOME_FROM_2026_07_20 = {"ベンス", "ペェーン", "ウムニー"}
RULE_POSITION_STAFF = {
    "6-15アド": {"トム", "ザン", "レー"},
    "学校アド": {"トム", "ザン", "レー"},
    "学校": {"ス"},
    "こ": {"ゾ", "トー", "ティ"},
    "が": {"チャン", "リン"},
    "がまえ": {"チャン", "リン"},
    "べ": {"チー", "セイン", "ベンス", "ペェーン"},
    # ガン・チンは6時-15時勤務のため、5時出勤のローテーション対象外。
    "5あ洗": {"チャン", "リン", "ハー", "タム", "ニー", "ベンス", "ペェーン", "ウムニー"},
    "6あ洗": {"ガン", "チン"},
    "ふた": {"ハー"},
    "うしろ": {"タム"},
}
PDF_REFERENCE_SOURCE_PAGE3_WEEK1 = "shift_pdf_page_3_week_1_manual"
PDF_REFERENCE_SOURCE_PAGE3_WEEK2 = "shift_pdf_page_3_week_2_manual"
PDF_WEEK1_START = date(2026, 6, 21)
PDF_WEEK1_END_EXCLUSIVE = date(2026, 6, 28)
PDF_WEEK2_START = date(2026, 6, 28)
PDF_WEEK2_END_EXCLUSIVE = date(2026, 7, 5)

PDF_WEEK1_MANUAL_CELLS = {
    "トム": ["○", "学校アド", "学校アド", "学校アド", "○", "学校アド", "○"],
    "ザン": ["6-15アド", "学校アド", "学校アド", "○", "学校アド", "学校アド", "6-15アド"],
    "レー": ["6-15アド", "学校アド", "○", "学校アド", "学校アド", "○", "6-15アド"],
    "ス": ["斜線", "学校", "学校", "学校", "学校", "学校", "○"],
    "ティ": ["○", "", "", "こ", "", "", "○"],
    "ゾ": ["斜線", "こ", "こ", "○", "こ", "こ", ""],
    "トー": ["○", "こ", "こ", "こ", "こ", "こ", ""],
    "キン": ["斜線", "", "", "○", "", "", "○"],
    "ピョー": ["○", "", "", "", "", "", "○"],
    "チー": ["斜線", "○", "べ", "べ", "○", "斜線", ""],
    "セイン": ["○", "べ", "べ", "べ", "べ", "べ", ""],
    "ベンス": ["斜線", "べ", "○", "べ", "べ", "斜線", "○"],
    "ペェーン": ["○", "", "べ", "", "○", "5あ洗", ""],
    "ウムニー": ["○", "○", "", "", "", "斜線", ""],
    "チャン": ["斜線", "", "が", "", "", "斜線", "○"],
    "リン": ["斜線", "がまえ", "○", "がまえ", "がまえ", "がまえ", ""],
    "ハー": ["○", "ふた", "ふた", "ふた", "ふた", "ふた", "○"],
    "タム": ["○", "5あ洗", "うしろ", "うしろ", "うしろ", "うしろ", ""],
    "ニー": ["斜線", "○", "5あ洗", "", "", "", "○"],
    "ガン": ["○", "", "", "6あ洗", "", "", "○"],
    "チン": ["斜線", "", "", "○", "6あ洗", "", ""],
}

PDF_WEEK2_MANUAL_CELLS = {
    "トム": ["○", "学校アド", "学校アド", "学校アド", "○", "○", "6-15アド"],
    "ザン": ["6-15アド", "○", "学校アド", "○", "学校アド", "学校アド", "○"],
    "レー": ["6-15アド", "学校アド", "○", "学校アド", "学校アド", "学校アド", "6-15アド"],
    "ス": ["○", "学校", "学校", "学校", "学校", "学校", "○"],
    "ティ": ["○", "", "こ", "こ", "", "", "○"],
    "ゾ": ["斜線", "こ", "こ", "○", "こ", "こ", ""],
    "トー": ["○", "こ", "○", "こ", "こ", "こ", "○"],
    "キン": ["斜線", "", "", "○", "", "", ""],
    "ピョー": ["斜線", "", "", "", "", "", "○"],
    "チー": ["斜線", "べ", "べ", "べ", "○", "べ", ""],
    "セイン": ["○", "べ", "べ", "べ", "べ", "べ", "○"],
    "ベンス": ["○", "べ", "5あ洗", "べ", "べ", "べ", "○"],
    "ペェーン": ["斜線", "○", "べ", "", "", "", ""],
    "ウムニー": ["○", "5あ洗", "", "", "○", "", ""],
    "チャン": ["斜線", "○", "", "5あ洗", "", "", ""],
    "リン": ["○", "がまえ", "がまえ", "がまえ", "5あ洗", "がまえ", ""],
    "ハー": ["斜線", "", "ふた", "ふた", "ふた", "○", ""],
    "タム": ["○", "うしろ", "うしろ", "うしろ", "うしろ", "5あ洗", "○"],
    "ニー": ["○", "", "", "", "", "", ""],
    "ガン": ["○", "", "", "", "", "", "○"],
    "チン": ["斜線", "○", "", "", "", "", "○"],
}

PDF_PAGE3_START = date(2026, 6, 21)
PDF_PAGE3_END_EXCLUSIVE = date(2026, 7, 21)
PDF_REFERENCE_SOURCE_PAGE3_FULL = "shift_pdf_page_3_full_manual"

# 目視確認したページ3の7/5〜7/20。前半14日間は上記の週次fixtureをそのまま使う。
PDF_PAGE3_DATES_5_TO_20 = {
    "トム": ["6-15アド", "○", "学校アド", "学校アド", "学校アド", "学校アド", "6-15アド", "6-15アド", "○", "学校アド", "学校アド", "学校アド", "○", "6-15アド", "6-15アド", "6-15アド"],
    "ザン": ["○", "学校アド", "学校アド", "○", "○", "学校アド", "6-15アド", "○", "学校アド", "学校アド", "○", "○", "6-15アド", "○", "6-15アド", "○"],
    "レー": ["6-15アド", "学校アド", "○", "○", "学校アド", "学校アド", "○", "6-15アド", "学校アド", "○", "学校アド", "学校アド", "6-15アド", "6-15アド", "○", "6-15アド"],
    "ス": ["○", "学校", "学校", "学校", "学校", "学校", "○", "○", "学校", "学校", "学校", "学校", "", "○", "斜線", "○"],
    "ティ": ["斜線", "", "", "", "", "こ", "○", "斜線", "こ", "○", "こ", "", "", "○", "斜線", ""],
    "ゾ": ["○", "こ", "こ", "こ", "こ", "○", "", "○", "こ", "こ", "○", "こ", "こ", "", "○", "○"],
    "トー": ["斜線", "こ", "こ", "こ", "こ", "○", "", "斜線", "○", "こ", "こ", "こ", "こ", "", "○", "○"],
    "キン": ["○", "", "", "", "", "", "○", "○", "", "", "", "", "", "", "○", ""],
    "ピョー": ["斜線", "", "", "", "", "", "○", "斜線", "", "", "", "", "", "○", "斜線", "○"],
    "チー": ["斜線", "べ", "べ", "べ", "○", "べ", "", "○", "べ", "べ", "べ", "○", "べ", "○", "○", ""],
    "セイン": ["○", "べ", "べ", "べ", "べ", "べ", "○", "斜線", "べ", "○", "べ", "べ", "べ", "", "斜線", "○"],
    "ベンス": ["斜線", "べ", "べ", "べ", "5あ洗", "○", "", "○", "べ", "べ", "べ", "べ", "べ", "", "○", ""],
    "ペェーン": ["○", "○", "", "べ", "", "べ", "", "斜線", "○", "5あ洗", "", "", "", "", "斜線", "○"],
    "ウムニー": ["斜線", "", "", "", "○", "", "", "斜線", "5あ洗", "○", "", "", "", "○", "斜線", ""],
    "チャン": ["○", "", "", "", "", "", "", "○", "", "", "5あ洗", "", "○", "", "○", "○"],
    "リン": ["○", "がまえ", "がまえ", "がまえ", "がまえ", "がまえ", "○", "○", "がまえ", "がまえ", "がまえ", "5あ洗", "がまえ", "○", "斜線", "○"],
    "ハー": ["斜線", "5あ洗", "ふた", "ふた", "ふた", "○", "", "○", "ふた", "○", "ふた", "ふた", "5あ洗", "○", "○", ""],
    "タム": ["○", "うしろ", "うしろ", "うしろ", "うしろ", "うしろ", "○", "斜線", "うしろ", "うしろ", "うしろ", "うしろ", "うしろ", "○", "斜線", "○"],
    "ニー": ["○", "", "5あ洗", "", "", "", "○", "斜線", "", "", "○", "", "", "", "○", ""],
    "ガン": ["斜線", "○", "", "6あ洗", "", "", "", "○", "", "", "", "", "", "", "○", ""],
    "チン": ["○", "", "", "", "", "6あ洗", "○", "斜線", "○", "", "", "", "", "", "○", ""],
}

PDF_PAGE3_MANUAL_CELLS = {
    staff_name: PDF_WEEK1_MANUAL_CELLS[staff_name]
    + PDF_WEEK2_MANUAL_CELLS[staff_name]
    + PDF_PAGE3_DATES_5_TO_20[staff_name]
    for staff_name in PDF_STAFF_NAMES
}

PDF_PAGE5_START = date(2026, 7, 21)
PDF_PAGE5_END_EXCLUSIVE = date(2026, 8, 21)
PDF_REFERENCE_SOURCE_PAGE5_FULL = "shift_pdf_page_5_full_manual"

# 目視確認したページ5（2026/7/21〜8/20）の31日分。左17列（21日〜6日）
# と右14列（7日〜20日）を連結している。空欄は通常勤務を表す。
PDF_PAGE5_LEFT_MANUAL_CELLS = {
    "トム": ["学校アド", "学校アド", "○", "学校アド", "○", "6-15アド", "学校アド", "学校アド", "学校アド", "○", "学校アド", "6-15アド", "6-15アド", "学校アド", "学校アド", "学校アド", "○"],
    "ザン": ["学校アド", "○", "学校アド", "学校アド", "6-15アド", "○", "学校アド", "○", "学校アド", "学校アド", "学校アド", "6-15アド", "○", "○", "学校アド", "学校アド", "学校アド"],
    "レー": ["○", "学校アド", "学校アド", "学校アド", "6-15アド", "6-15アド", "○", "学校アド", "学校アド", "学校アド", "学校アド", "○", "6-15アド", "学校アド", "○", "○", "学校アド"],
    "ス": ["", "", "", "", "", "斜線", "○", "", "", "", "", "○", "斜線", "", "", "", ""],
    "ティ": ["", "", "", "", "○", "斜線", "", "○", "", "", "", "", "○", "", "○", "", ""],
    "ゾ": ["", "", "", "", "", "○", "", "", "", "", "", "○", "斜線", "", "", "○", "○"],
    "トー": ["", "", "", "", "○", "斜線", "", "", "", "", "", "○", "斜線", "", "", "", ""],
    "キン": ["", "", "", "", "○", "斜線", "", "", "", "", "", "○", "斜線", "", "", "", ""],
    "ピョー": ["", "", "", "", "", "○", "", "", "", "", "", "", "○", "", "", "", ""],
    "チー": ["", "", "", "", "○", "斜線", "", "○", "", "", "", "", "○", "", "", "", ""],
    "セイン": ["", "", "", "", "", "○", "○", "", "", "", "", "", "斜線", "○", "", "", ""],
    "ベンス": [""] * 17,
    "ペェーン": [""] * 17,
    "ウムニー": [""] * 17,
    "チャン": ["", "", "", "", "", "○", "5あ洗", "", "", "", "", "○", "○", "", "", "○", "5あ洗"],
    "リン": ["", "", "", "", "", "斜線", "○", "5あ洗", "", "", "", "", "斜線", "○", "", "5あ洗", ""],
    "ハー": ["ふた", "ふた", "ふた", "ふた", "", "○", "ふた", "ふた", "5あ洗", "ふた", "ふた", "", "○", "ふた", "ふた", "ふた", "ふた"],
    "タム": ["5あ洗", "うしろ", "うしろ", "うしろ", "○", "斜線", "うしろ", "うしろ", "うしろ", "5あ洗", "うしろ", "○", "斜線", "うしろ", "うしろ", "うしろ", "うしろ"],
    "ニー": ["", "5あ洗", "", "", "", "○", "", "", "", "", "5あ洗", "", "○", "", "", "", ""],
    "ガン": ["", "", "6あ洗", "", "○", "斜線", "", "", "", "", "", "○", "斜線", "6あ洗", "", "", ""],
    "チン": ["", "", "", "6あ洗", "", "○", "", "", "", "", "", "", "○", "", "6あ洗", "", "○"],
}

PDF_PAGE5_RIGHT_MANUAL_CELLS = {
    "トム": ["6-15アド", "6-15アド", "○", "学校アド", "学校アド", "○", "学校アド", "学校アド", "6-15アド", "○", "○", "学校アド", "学校アド", "○"],
    "ザン": ["学校アド", "○", "○", "学校アド", "学校アド", "○", "学校アド", "学校アド", "○", "6-15アド", "学校アド", "学校アド", "○", "6-15アド"],
    "レー": ["○", "6-15アド", "6-15アド", "学校アド", "○", "学校アド", "学校アド", "○", "6-15アド", "6-15アド", "学校アド", "○", "学校アド", "6-15アド"],
    "ス": ["○", "", "斜線", "○", "○", "斜線", "斜線", "○", "", "斜線", "○", "", "", ""],
    "ティ": ["", "", "○", "斜線", "斜線", "○", "○", "斜線", "○", "○", "", "", "", ""],
    "ゾ": ["○", "", "斜線", "○", "○", "斜線", "斜線", "○", "", "斜線", "", "", "", ""],
    "トー": ["○", "○", "", "斜線", "斜線", "○", "○", "斜線", "", "○", "", "", "○", ""],
    "キン": ["○", "○", "斜線", "斜線", "", "○", "斜線", "斜線", "", "○", "", "", "", ""],
    "ピョー": ["○", "", "斜線", "○", "斜線", "○", "○", "斜線", "○", "斜線", "", "", "", ""],
    "チー": ["○", "", "斜線", "○", "斜線", "斜線", "○", "斜線", "○", "○", "", "", "", ""],
    "セイン": ["", "○", "斜線", "○", "○", "斜線", "○", "○", "", "斜線", "", "○", "", ""],
    "ベンス": [""] * 14,
    "ペェーン": [""] * 14,
    "ウムニー": [""] * 14,
    "チャン": ["○", "○", "斜線", "○", "○", "斜線", "斜線", "○", "", "斜線", "", "○", "", ""],
    "リン": ["", "", "斜線", "○", "○", "斜線", "○", "○", "", "斜線", "", "", "", ""],
    "ハー": ["5あ洗", "○", "斜線", "斜線", "○", "斜線", "斜線", "○", "○", "○", "ふた", "ふた", "○", "ふた"],
    "タム": ["うしろ", "○", "斜線", "斜線", "○", "○", "斜線", "○", "", "斜線", "5あ洗", "うしろ", "うしろ", "うしろ"],
    "ニー": ["", "○", "○", "斜線", "斜線", "○", "斜線", "○", "○", "斜線", "", "5あ洗", "", "○"],
    "ガン": ["", "○", "斜線", "○", "○", "斜線", "斜線", "○", "", "斜線", "", "", "6あ洗", ""],
    "チン": ["", "○", "斜線", "斜線", "○", "○", "斜線", "○", "○", "斜線", "", "", "", "6あ洗"],
}

PDF_PAGE5_MANUAL_CELLS = {
    staff_name: PDF_PAGE5_LEFT_MANUAL_CELLS[staff_name]
    + PDF_PAGE5_RIGHT_MANUAL_CELLS[staff_name]
    for staff_name in PDF_STAFF_NAMES
}

ASSIGNMENT_LABELS = sorted(
    {
        label
        for cells in (
            PDF_WEEK1_MANUAL_CELLS,
            PDF_WEEK2_MANUAL_CELLS,
            PDF_PAGE3_MANUAL_CELLS,
            PDF_PAGE5_MANUAL_CELLS,
        )
        for labels in cells.values()
        for label in labels
        if label not in {"", "○", "斜線"}
    }
)


def seed_pdf_shift_data(session: Session, source_name: str = "shift_pdf_2026") -> None:
    with dev_seed_write_snapshot(session):
        _populate_pdf_shift_data(session, source_name)


def _populate_pdf_shift_data(session: Session, source_name: str) -> None:
    if session.query(Staff).filter(Staff.name == "トム").first():
        return

    school_ad = Skill(name="school_ad")
    general_work = Skill(name="general_work")
    gohan = Skill(name="gohan")
    special_order = Skill(name="special_order")
    prep = Skill(name="prep")
    fry = Skill(name="fry")
    early_5 = Skill(name="early_5")
    session.add_all([school_ad, general_work, gohan, special_order, prep, fry, early_5])
    session.flush()

    staff_by_name = {
        name: Staff(
            name=name,
            staff_group="trainee" if name in PRACTICAL_TRAINEES else "specific",
        )
        for name in PDF_STAFF_NAMES
    }
    session.add_all(staff_by_name.values())
    session.flush()

    for name, staff in staff_by_name.items():
        session.add(StaffSkill(staff_id=staff.id, skill_id=general_work.id))
        if name in SCHOOL_AD_STAFF:
            session.add(StaffSkill(staff_id=staff.id, skill_id=school_ad.id))
        if name in GOHAN_STAFF:
            session.add(StaffSkill(staff_id=staff.id, skill_id=gohan.id))
        if name in {"チー", "セイン"}:
            session.add(StaffSkill(staff_id=staff.id, skill_id=special_order.id))
        if name in {"チャン", "ティ"}:
            session.add(StaffSkill(staff_id=staff.id, skill_id=prep.id))
        if name in {"キン", "ス"}:
            session.add(StaffSkill(staff_id=staff.id, skill_id=fry.id))
        if name in PRACTICAL_TRAINEES:
            session.add(StaffSkill(staff_id=staff.id, skill_id=early_5.id))

    day_shift = ShiftTemplate(
        name="通常勤務", shift_type="regular", start_time=time(8, 0), end_time=time(17, 0), break_minutes=60
    )
    school_shift = ShiftTemplate(
        name="6-15アド", shift_type="early", start_time=time(6, 0), end_time=time(15, 0), break_minutes=60
    )
    half_shift = ShiftTemplate(
        name="半日勤務", shift_type="half_day", start_time=time(8, 0), end_time=time(12, 0), break_minutes=0
    )
    early_shift = ShiftTemplate(
        name="5時出勤", shift_type="early", start_time=time(5, 0), end_time=time(14, 0), break_minutes=60
    )
    session.add_all([day_shift, school_shift, half_shift, early_shift])
    session.flush()

    positions = {
        "学校アド": Position(name="学校アド", required_skill_id=school_ad.id, difficulty_weight=2),
        "ごはん": Position(name="ごはん", required_skill_id=gohan.id),
        "学校ごはん": Position(name="学校ごはん", required_skill_id=gohan.id),
        "別注": Position(name="別注", required_skill_id=special_order.id),
        "仕込み": Position(name="仕込み", required_skill_id=prep.id),
        "揚げ": Position(name="揚げ", required_skill_id=fry.id),
        "5時出勤": Position(name="5時出勤", required_skill_id=early_5.id, difficulty_weight=2),
        "通常出勤": Position(name="通常出勤", required_skill_id=general_work.id),
        "半日出勤": Position(name="半日出勤", required_skill_id=general_work.id),
    }
    session.add_all(positions.values())
    session.flush()

    # PDF periods use 21st through next month 20th. The scanned PDF appears to align with 2026.
    _seed_period_demands(
        session=session,
        start=date(2026, 6, 21),
        end=date(2026, 7, 20),
        day_shift_id=day_shift.id,
        school_shift_id=school_shift.id,
        half_shift_id=half_shift.id,
        early_shift_id=early_shift.id,
        position_by_name=positions,
        extra_half_day_dates=set(),
    )
    _seed_period_workday_rules(
        session=session,
        start=date(2026, 6, 21),
        end=date(2026, 7, 20),
        day_shift_id=day_shift.id,
        half_shift_id=half_shift.id,
        full_day_shift_id=school_shift.id,
    )
    _seed_period_demands(
        session=session,
        start=date(2026, 7, 21),
        end=date(2026, 8, 20),
        day_shift_id=day_shift.id,
        school_shift_id=school_shift.id,
        half_shift_id=half_shift.id,
        early_shift_id=early_shift.id,
        position_by_name=positions,
        extra_half_day_dates={date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)},
    )
    _seed_period_workday_rules(
        session=session,
        start=date(2026, 7, 21),
        end=date(2026, 8, 20),
        day_shift_id=day_shift.id,
        half_shift_id=half_shift.id,
        full_day_shift_id=school_shift.id,
        extra_half_day_dates={date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)},
    )

    # 7/20 onward: ベンス, ペェーン, ウムニー帰国.
    for name in ["ベンス", "ペェーン", "ウムニー"]:
        staff = staff_by_name[name]
        staff.active_to = date(2026, 7, 19)
        for work_date in _date_range(date(2026, 7, 20), date(2026, 8, 21)):
            session.add(
                Availability(
                    staff_id=staff.id,
                    work_date=work_date,
                    status="unavailable",
                    strength="hard",
                    reason=f"{source_name}: return-home period from PDF rule page",
                )
            )

    # Pair-related PDF rules are represented first as availability pressure by not creating
    # pair-specific constraints yet. They are listed in docs as a known remaining solver feature.


def seed_pdf_week1_manual_data(session: Session) -> None:
    """Seed only the manually read PDF week for deterministic parity checks."""
    _seed_pdf_manual_data(
        session=session,
        target_start=PDF_WEEK1_START,
        target_end_exclusive=PDF_WEEK1_END_EXCLUSIVE,
        target_cells=PDF_WEEK1_MANUAL_CELLS,
        reference_source=PDF_REFERENCE_SOURCE_PAGE3_WEEK1,
    )


def seed_pdf_week2_from_week1_calibration_data(session: Session) -> None:
    """Seed week 2 target while keeping skills/preferences calibrated from week 1 only."""
    _seed_pdf_manual_data(
        session=session,
        target_start=PDF_WEEK2_START,
        target_end_exclusive=PDF_WEEK2_END_EXCLUSIVE,
        target_cells=PDF_WEEK2_MANUAL_CELLS,
        reference_source=PDF_REFERENCE_SOURCE_PAGE3_WEEK2,
    )


def seed_pdf_page3_full_manual_data(session: Session) -> None:
    """Seed every visible staff/date cell on PDF page 3 for a full-period parity check."""
    _seed_pdf_manual_data(
        session=session,
        target_start=PDF_PAGE3_START,
        target_end_exclusive=PDF_PAGE3_END_EXCLUSIVE,
        target_cells=PDF_PAGE3_MANUAL_CELLS,
        reference_source=PDF_REFERENCE_SOURCE_PAGE3_FULL,
    )


def seed_pdf_page5_full_manual_data(session: Session) -> None:
    """Seed every visible staff/date cell on PDF page 5 for a full-period parity check."""
    _seed_pdf_manual_data(
        session=session,
        target_start=PDF_PAGE5_START,
        target_end_exclusive=PDF_PAGE5_END_EXCLUSIVE,
        target_cells=PDF_PAGE5_MANUAL_CELLS,
        reference_source=PDF_REFERENCE_SOURCE_PAGE5_FULL,
    )


def _seed_pdf_manual_data(
    session: Session,
    target_start: date,
    target_end_exclusive: date,
    target_cells: dict[str, list[str]],
    reference_source: str,
) -> None:
    with dev_seed_write_snapshot(session):
        _populate_pdf_manual_data(session, target_start, target_end_exclusive, target_cells, reference_source)


def _populate_pdf_manual_data(
    session: Session,
    target_start: date,
    target_end_exclusive: date,
    target_cells: dict[str, list[str]],
    reference_source: str,
) -> None:
    """Load one manually checked PDF period as structured local fixture data.

    Source-derived schedule events are delegated to
    ``pdf_reference_fixtures`` so the generic seed path and solver remain
    independent of customer-specific exceptions.
    """
    if session.query(Staff).filter(Staff.name == "トム").first():
        return

    staff_by_name = {
        name: Staff(
            name=name,
            staff_group="trainee" if name in PRACTICAL_TRAINEES else "specific",
        )
        for name in PDF_STAFF_NAMES
    }
    session.add_all(staff_by_name.values())
    session.flush()

    if target_start == PDF_PAGE5_START:
        for staff_name in RETURN_HOME_FROM_2026_07_20:
            staff_by_name[staff_name].active_to = date(2026, 7, 19)

    rule_config = {
        "source": "Shift.pdf page 1",
        "period_rule": "21_to_next_20",
        "weekly_min_off_days": 1,
        # PDFの個別休暇セルを正本として扱うため、月8日をハード下限にはしない。
        # 目視結果には7日休みのスタッフもいるため、月休数は後段で要確認とする。
        "monthly_min_off_days": 0,
        "monthly_max_off_days": 12,
        "max_consecutive_work_days": 6,
        "require_monthly_consecutive_off": True,
        "monthly_consecutive_off_strength": "soft",
        "monthly_consecutive_off_weight": 20,
        "avoid_unplanned_off_weight": 10,
        "weekday_off_spread_weight": 1,
        "weekday_off_target_min": 2,
        "weekday_off_target_max": 3,
    }

    session.add(
        RuleConfig(
            name="active",
            config_json=rule_config,
        )
    )
    session.add_all(
        [
            StaffPairRule(
                first_staff_id=min(staff_by_name["チャン"].id, staff_by_name["ティ"].id),
                second_staff_id=max(staff_by_name["チャン"].id, staff_by_name["ティ"].id),
                kind="avoid_same_weekday_off",
                strength="hard",
                reason="PDF検証用の平日休み条件",
            ),
            StaffPairRule(
                first_staff_id=min(staff_by_name["キン"].id, staff_by_name["ス"].id),
                second_staff_id=max(staff_by_name["キン"].id, staff_by_name["ス"].id),
                kind="avoid_same_weekday_off",
                strength="soft",
                weight=10,
                reason="PDF検証用の平日休み条件",
            ),
        ]
    )

    skill_by_label = {
        label: Skill(name=f"pdf_week1_position:{label}") for label in ASSIGNMENT_LABELS
    }
    session.add_all(skill_by_label.values())
    session.flush()

    for label, staff_names in RULE_POSITION_STAFF.items():
        for staff_name in staff_names:
            session.add(
                StaffSkill(
                    staff_id=staff_by_name[staff_name].id,
                    skill_id=skill_by_label[label].id,
                )
            )

    day_shift = ShiftTemplate(
        name="PDF日勤", display_name="通常勤務", shift_type="regular", start_time=time(8, 0), end_time=time(17, 0), break_minutes=60
    )
    early_5_shift = ShiftTemplate(
        name="PDF5時", display_name="5時勤務", shift_type="early", start_time=time(5, 0), end_time=time(14, 0), break_minutes=60
    )
    early_6_shift = ShiftTemplate(
        name="PDF6時", display_name="6時勤務", shift_type="early", start_time=time(6, 0), end_time=time(15, 0), break_minutes=60
    )
    half_shift = ShiftTemplate(
        name="PDF半日", display_name="半日勤務", shift_type="half_day", start_time=time(8, 0), end_time=time(12, 0), break_minutes=0
    )
    session.add_all([day_shift, early_5_shift, early_6_shift, half_shift])
    session.flush()

    shifts_by_label = {
        "5あ洗": early_5_shift,
        "6あ洗": early_6_shift,
        "6-15アド": early_6_shift,
    }
    dates = list(_date_range(target_start, target_end_exclusive))
    extra_half_day_dates = (
        {
            date(2026, 8, 10),
            date(2026, 8, 11),
            date(2026, 8, 12),
            date(2026, 8, 13),
            date(2026, 8, 14),
        }
        if target_start == PDF_PAGE5_START
        else set()
    )
    workday_rules = []
    for work_date in dates:
        is_half_day = work_date.weekday() == 6 or work_date in extra_half_day_dates
        workday_rules.append(
            WorkdayRule(
                work_date=work_date,
                kind="half_day" if is_half_day else "normal",
                shift_template_id=half_shift.id if is_half_day else day_shift.id,
                full_day_shift_template_id=day_shift.id if is_half_day else None,
                full_day_staff_ids=[
                    staff_by_name[name].id
                    for name in PDF_STAFF_NAMES
                    if name in SCHOOL_AD_STAFF
                    or target_cells[name][(work_date - target_start).days] == ""
                ]
                if is_half_day
                else [],
            )
        )
    session.add_all(workday_rules)
    positions_by_label = {
        label: Position(name=label, required_skill_id=skill_by_label[label].id)
        for label in ASSIGNMENT_LABELS
    }
    session.add_all(positions_by_label.values())
    session.flush()

    initial_queue_names = (
        ["タム", "ニー", "チャン", "リン", "ハー"]
        if target_start == PDF_PAGE5_START
        else ["タム", "ニー", "ベンス", "ペェーン", "ウムニー", "チャン", "リン", "ハー"]
    )
    rotation = RotationPolicy(
        name="PDF 5時洗浄ローテーション",
        position_id=positions_by_label["5あ洗"].id,
        member_staff_ids=[staff_by_name[name].id for name in initial_queue_names],
    )
    session.add(rotation)
    session.add(
        RotationPolicy(
            name="PDF 6時洗浄ローテーション",
            position_id=positions_by_label["6あ洗"].id,
            member_staff_ids=[staff_by_name["ガン"].id, staff_by_name["チン"].id],
        )
    )
    session.flush()
    if target_start == PDF_WEEK2_START:
        carried_queue = [staff_by_name[name].id for name in initial_queue_names]
        for selected_name in ("タム", "ニー", "ペェーン"):
            carried_queue.remove(staff_by_name[selected_name].id)
            carried_queue.append(staff_by_name[selected_name].id)
        session.add(
            RotationQueueState(
                rotation_policy_id=rotation.id,
                effective_from=target_start,
                queue_staff_ids=carried_queue,
            )
        )
    add_pdf_source_derived_schedule_exceptions(
        session,
        reference_source=reference_source,
        staff_by_name=staff_by_name,
        rotation_policy_id=rotation.id,
    )
    for rank, staff_name in enumerate(["ゾ", "トー", "ティ"], start=1):
        session.add(
            PositionPriority(
                position_id=positions_by_label["こ"].id,
                staff_id=staff_by_name[staff_name].id,
                rank=rank,
                mode="strict",
            )
        )
    for label in ("が", "がまえ"):
        for rank, staff_name in enumerate(["リン", "チャン"], start=1):
            session.add(
                PositionPriority(
                    position_id=positions_by_label[label].id,
                    staff_id=staff_by_name[staff_name].id,
                    rank=rank,
                    mode="strict",
                )
            )
    for label, position in positions_by_label.items():
        if label == "こ":
            continue
        session.add(
            StaffPositionRule(
                staff_id=staff_by_name["ゾ"].id,
                position_id=position.id,
                kind="deny",
                reason="ゾさんはごはん担当のみ",
            )
        )

    source_page = 5 if reference_source == PDF_REFERENCE_SOURCE_PAGE5_FULL else 3
    for col_index, work_date in enumerate(dates):
        counts_by_label: dict[str, int] = {}
        for row_index, staff_name in enumerate(PDF_STAFF_NAMES):
            label = target_cells[staff_name][col_index]
            staff = staff_by_name[staff_name]
            if label == "○":
                session.add(
                    Availability(
                        staff_id=staff.id,
                        work_date=work_date,
                        status="unavailable",
                        strength="hard",
                        reason=f"{reference_source}: {label}",
                    )
                )
                reference_status = ("off", "off")
            elif label == "斜線":
                session.add(
                    Availability(
                        staff_id=staff.id,
                        work_date=work_date,
                        status="half_day",
                        strength="hard",
                        shift_template_id=half_shift.id,
                        reason=f"{reference_source}: {label}",
                    )
                )
                reference_status = ("working", "half_day")
            elif not label:
                session.add(
                    Availability(
                        staff_id=staff.id,
                        work_date=work_date,
                        status="request_work",
                        strength="hard",
                        reason=f"{reference_source}: 空欄は通常8時間勤務",
                    )
                )
                reference_status = (
                    "working",
                    "regular",
                )
            else:
                reference_status = ("working", "position")

            session.add(
                ReferenceStaffStatus(
                    source_name=reference_source,
                    staff_id=staff.id,
                    work_date=work_date,
                    status=reference_status[0],
                    work_kind=reference_status[1],
                    raw_label=label,
                    confidence="high",
                    source_page=source_page,
                    source_row_index=row_index,
                    source_col_index=col_index,
                )
            )
            if not label or label in {"○", "斜線"}:
                continue

            counts_by_label[label] = counts_by_label.get(label, 0) + 1
            shift = shifts_by_label.get(label, day_shift)
            position = positions_by_label[label]
            session.add(
                ReferenceAssignment(
                    source_name=reference_source,
                    staff_id=staff.id,
                    work_date=work_date,
                    shift_template_id=shift.id,
                    position_id=position.id,
                    raw_label=label,
                    confidence="high",
                    source_page=source_page,
                    source_row_index=row_index,
                    source_col_index=col_index,
                )
            )

        for label, required_count in counts_by_label.items():
            shift = shifts_by_label.get(label, day_shift)
            position = positions_by_label[label]
            session.add(
                Demand(
                    work_date=work_date,
                    shift_template_id=shift.id,
                    position_id=position.id,
                    required_count=required_count,
                )
            )



def _seed_period_demands(
    session: Session,
    start: date,
    end: date,
    day_shift_id: int,
    school_shift_id: int,
    half_shift_id: int,
    early_shift_id: int,
    position_by_name: dict[str, Position],
    extra_half_day_dates: set[date] | None = None,
) -> None:
    extra_half_day_dates = extra_half_day_dates or set()
    for work_date in _date_range(start, end + timedelta(days=1)):
        is_weekend = work_date.weekday() >= 5
        is_half_day = work_date.weekday() == 6 or work_date in extra_half_day_dates
        regular_shift_id = half_shift_id if is_half_day else day_shift_id
        school_ad_count = 1 if is_weekend else 2
        session.add(
            Demand(
                work_date=work_date,
                shift_template_id=school_shift_id,
                position_id=position_by_name["学校アド"].id,
                required_count=school_ad_count,
            )
        )

        if is_weekend:
            session.add(
                Demand(
                    work_date=work_date,
                    shift_template_id=regular_shift_id,
                    position_id=position_by_name["半日出勤" if work_date.weekday() == 6 else "通常出勤"].id,
                    required_count=6,
                )
            )
            session.add(
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["ごはん"].id,
                        required_count=1,
                    )
            )
        else:
            session.add_all(
                [
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["ごはん"].id,
                        required_count=2,
                    ),
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["学校ごはん"].id,
                        required_count=1,
                    ),
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["別注"].id,
                        required_count=1,
                    ),
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["仕込み"].id,
                        required_count=1,
                    ),
                    Demand(
                        work_date=work_date,
                        shift_template_id=regular_shift_id,
                        position_id=position_by_name["揚げ"].id,
                        required_count=1,
                    ),
                ]
            )
            if work_date.weekday() in {0, 2, 4} and not is_half_day:
                session.add(
                    Demand(
                        work_date=work_date,
                        shift_template_id=early_shift_id,
                        position_id=position_by_name["5時出勤"].id,
                        required_count=1,
                    )
                )


def _seed_period_workday_rules(
    session: Session,
    start: date,
    end: date,
    day_shift_id: int,
    half_shift_id: int,
    full_day_shift_id: int | None = None,
    extra_half_day_dates: set[date] | None = None,
) -> None:
    extra_half_day_dates = extra_half_day_dates or set()
    staff_by_name = {staff.name: staff for staff in session.query(Staff).all()}
    session.add_all(
        [
            WorkdayRule(
                work_date=work_date,
                kind="half_day"
                if work_date.weekday() == 6 or work_date in extra_half_day_dates
                else "normal",
                shift_template_id=(
                    half_shift_id
                    if work_date.weekday() == 6 or work_date in extra_half_day_dates
                    else day_shift_id
                ),
                full_day_shift_template_id=(
                    full_day_shift_id
                    if work_date.weekday() == 6 or work_date in extra_half_day_dates
                    else None
                ),
                full_day_staff_ids=(
                    [staff_by_name[name].id for name in SCHOOL_AD_STAFF]
                    if work_date.weekday() == 6 or work_date in extra_half_day_dates
                    else []
                ),
                group_min_counts={"specific": 1} if work_date.weekday() >= 5 else {},
            )
            for work_date in _date_range(start, end + timedelta(days=1))
        ]
    )


def _date_range(start: date, end_exclusive: date):
    current = start
    while current < end_exclusive:
        yield current
        current += timedelta(days=1)
