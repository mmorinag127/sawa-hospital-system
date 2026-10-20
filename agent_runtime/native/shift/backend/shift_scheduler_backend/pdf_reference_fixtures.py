"""Source-derived fixtures used only for the manually checked Shift.pdf.

These rows are not application defaults and are not solver branches. They are
the structured representation of schedule events explicitly described in the
operator's email or observed in the checked reference table. Production data
is entered through the rules API/UI instead of this module.
"""

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from sqlalchemy.orm import Session

from .models import ScheduleException, Staff


@dataclass(frozen=True)
class PdfScheduleExceptionFixture:
    reference_source: str
    work_date: date
    kind: str
    staff_name: str
    reason: str
    evidence: str


PDF_SOURCE_DERIVED_SCHEDULE_EXCEPTIONS = (
    PdfScheduleExceptionFixture(
        reference_source="shift_pdf_page_3_week_1_manual",
        work_date=date(2026, 6, 26),
        kind="rotation_force",
        staff_name="ペェーン",
        reason="休み予定日の前に担当を前倒しする",
        evidence="担当者メール回答: 6/29が休みのため6/26に前倒し",
    ),
    PdfScheduleExceptionFixture(
        reference_source="shift_pdf_page_3_week_2_manual",
        work_date=date(2026, 6, 29),
        kind="rotation_force",
        staff_name="ウムニー",
        reason="別注担当の引継ぎ中は別の人を先に担当にする",
        evidence="担当者メール回答: ベンスが引継ぎ中のためウムニーを先に担当",
    ),
    PdfScheduleExceptionFixture(
        reference_source="shift_pdf_page_3_full_manual",
        work_date=date(2026, 6, 26),
        kind="rotation_force",
        staff_name="ペェーン",
        reason="休み予定日の前に担当を前倒しする",
        evidence="担当者メール回答: 6/29が休みのため6/26に前倒し",
    ),
    PdfScheduleExceptionFixture(
        reference_source="shift_pdf_page_3_full_manual",
        work_date=date(2026, 6, 29),
        kind="rotation_force",
        staff_name="ウムニー",
        reason="別注担当の引継ぎ中は別の人を先に担当にする",
        evidence="担当者メール回答: ベンスが引継ぎ中のためウムニーを先に担当",
    ),
    PdfScheduleExceptionFixture(
        reference_source="shift_pdf_page_3_full_manual",
        work_date=date(2026, 7, 9),
        kind="rotation_force",
        staff_name="ベンス",
        reason="参照表に記載された交代順を再現する",
        evidence="Shift.pdf page 3 の目視確認セル",
    ),
)


def add_pdf_source_derived_schedule_exceptions(
    session: Session,
    *,
    reference_source: str,
    staff_by_name: Mapping[str, Staff],
    rotation_policy_id: int,
) -> None:
    """Persist only the source events belonging to one PDF reference fixture."""
    rows = [
        fixture
        for fixture in PDF_SOURCE_DERIVED_SCHEDULE_EXCEPTIONS
        if fixture.reference_source == reference_source
    ]
    if not rows:
        return
    missing_names = sorted(
        {fixture.staff_name for fixture in rows if fixture.staff_name not in staff_by_name}
    )
    if missing_names:
        raise ValueError(f"PDF fixture staff not found: {missing_names}")
    session.add_all(
        [
            ScheduleException(
                work_date=fixture.work_date,
                kind=fixture.kind,
                staff_id=staff_by_name[fixture.staff_name].id,
                rotation_policy_id=rotation_policy_id,
                reason=f"{fixture.reason} [{fixture.evidence}]",
            )
            for fixture in rows
        ]
    )
