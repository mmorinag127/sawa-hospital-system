from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .models import (
    Assignment,
    CandidateStaffStatus,
    ReferenceAssignment,
    ReferenceStaffStatus,
    Staff,
)


STATUS_LABELS = {
    "off": "○",
    "half_day": "斜線",
    "regular": "",
}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2, "unreadable": 3}


def group_candidate_assignments(assignments: list[Assignment]) -> dict[tuple[int, date], list[Assignment]]:
    """Keep every assignment in source read order, without choosing a cell winner."""
    cells: dict[tuple[int, date], list[Assignment]] = defaultdict(list)
    for row in assignments:
        cells[(row.staff_id, row.work_date)].append(row)
    return cells


def _assignment_cell_label(assignments: list[Assignment]) -> str:
    if len(assignments) == 1:
        return assignments[0].position.name
    return "\n".join(f"{row.position.name} / {row.shift_template.name}" for row in assignments)


def compare_manual_cells(
    session: Session,
    candidate_id: int | None,
    staff_names: list[str],
    start_date: date,
    expected_cells: dict[str, list[str]] | None = None,
    end_date: date | None = None,
    reference_source: str | None = None,
) -> list[dict[str, str]]:
    """Compare every staff/date cell, including ordinary and half-day states.

    ``expected_cells`` is kept for compatibility with the original local
    fixtures. New callers should pass ``reference_source`` so the persisted
    manually verified reference table is the source of truth.
    """
    if reference_source:
        if end_date is None:
            raise ValueError("end_date is required when reference_source is used")
        return compare_reference_cells(
            session=session,
            candidate_id=candidate_id,
            source_name=reference_source,
            staff_names=staff_names,
            start_date=start_date,
            end_date=end_date,
        )
    if expected_cells is None:
        raise ValueError("expected_cells or reference_source is required")

    if candidate_id is None:
        return [
            {
                "work_date": str(start_date),
                "staff": "(candidate)",
                "pdf_label": "created",
                "solver_label": "",
            }
        ]

    staff_by_id = {row.id: row.name for row in session.query(Staff).all()}
    actual = _candidate_cells(session, candidate_id, staff_by_id)

    mismatches: list[dict[str, str]] = []
    for staff_name in staff_names:
        for index, pdf_label in enumerate(expected_cells[staff_name]):
            work_date = start_date + timedelta(days=index)
            solver_label = actual.get((staff_name, work_date), "")
            if pdf_label != solver_label:
                mismatches.append(
                    {
                        "work_date": str(work_date),
                        "staff": staff_name,
                        "pdf_label": pdf_label,
                        "solver_label": solver_label,
                    }
                )
    return mismatches


def compare_reference_cells(
    session: Session,
    candidate_id: int | None,
    source_name: str,
    staff_names: list[str],
    start_date: date,
    end_date: date,
) -> list[dict[str, str]]:
    """Compare a candidate to persisted, manually verified reference cells.

    The reference table is intentionally separate from solver inputs. A
    missing or low-confidence cell is reported as an evidence problem instead
    of being treated as an empty PDF cell.
    """
    staff_by_id = {row.id: row.name for row in session.query(Staff).all()}
    reference_statuses = (
        session.query(ReferenceStaffStatus)
        .filter(ReferenceStaffStatus.source_name == source_name)
        .filter(ReferenceStaffStatus.work_date >= start_date)
        .filter(ReferenceStaffStatus.work_date < end_date)
        .all()
    )
    reference_assignments = (
        session.query(ReferenceAssignment)
        .filter(ReferenceAssignment.source_name == source_name)
        .filter(ReferenceAssignment.work_date >= start_date)
        .filter(ReferenceAssignment.work_date < end_date)
        .all()
    )
    assignments_by_cell: dict[tuple[int, date], list[ReferenceAssignment]] = defaultdict(list)
    for row in reference_assignments:
        assignments_by_cell[(row.staff_id, row.work_date)].append(row)
    statuses_by_cell = {
        (row.staff_id, row.work_date): row for row in reference_statuses
    }

    actual = _candidate_cells(session, candidate_id, staff_by_id)
    mismatches: list[dict[str, str]] = []
    total_days = (end_date - start_date).days
    for staff_name in staff_names:
        staff_id = next((id_ for id_, name in staff_by_id.items() if name == staff_name), None)
        for index in range(total_days):
            work_date = start_date + timedelta(days=index)
            key = (staff_id, work_date) if staff_id is not None else None
            status = statuses_by_cell.get(key) if key else None
            assignments = assignments_by_cell.get(key, []) if key else []
            expected_label: str | None = None
            issue: str | None = None
            confidence = "high"
            if staff_id is None:
                issue = "staff_missing"
            elif status is None:
                issue = "reference_cell_missing"
            elif status.work_kind == "position":
                if len(assignments) != 1:
                    issue = "reference_assignment_ambiguous"
                else:
                    expected_label = assignments[0].position.name
                    confidence = _lower_confidence(status.confidence, assignments[0].confidence)
            elif status.status == "off" or status.work_kind == "off":
                expected_label = STATUS_LABELS["off"]
                confidence = status.confidence
            elif status.work_kind == "half_day":
                expected_label = STATUS_LABELS["half_day"]
                confidence = status.confidence
            elif status.work_kind == "regular":
                expected_label = STATUS_LABELS["regular"]
                confidence = status.confidence
            else:
                issue = "reference_cell_unrecognized"

            if issue is None and confidence not in {"high", "medium"}:
                issue = "reference_cell_low_confidence"
            solver_label = actual.get((staff_name, work_date), "")
            if issue is not None or expected_label != solver_label:
                mismatch = {
                    "work_date": str(work_date),
                    "staff": staff_name,
                    "pdf_label": expected_label or "",
                    "solver_label": solver_label,
                }
                if issue is not None:
                    mismatch["issue"] = issue
                mismatches.append(mismatch)
    return mismatches


def _lower_confidence(first: str, second: str) -> str:
    return max(
        (first, second),
        key=lambda value: CONFIDENCE_RANK.get(value, max(CONFIDENCE_RANK.values()) + 1),
    )


def _candidate_cells(
    session: Session,
    candidate_id: int | None,
    staff_by_id: dict[int, str],
) -> dict[tuple[str, date], str]:
    if candidate_id is None:
        return {}
    actual: dict[tuple[str, date], str] = {}
    assignments = (
        session.query(Assignment)
        .filter(Assignment.candidate_id == candidate_id)
        .order_by(Assignment.work_date, Assignment.staff_id, Assignment.id)
        .all()
    )
    for (staff_id, work_date), rows in group_candidate_assignments(assignments).items():
        actual[(staff_by_id[staff_id], work_date)] = _assignment_cell_label(rows)
    for row in (
        session.query(CandidateStaffStatus)
        .filter(CandidateStaffStatus.candidate_id == candidate_id)
        .order_by(CandidateStaffStatus.work_date, CandidateStaffStatus.staff_id)
        .all()
    ):
        key = (staff_by_id[row.staff_id], row.work_date)
        if key in actual:
            continue
        actual[key] = STATUS_LABELS.get(row.work_kind, "")
    return actual


def reference_status_count(session: Session, source_name: str) -> int:
    return (
        session.query(ReferenceStaffStatus)
        .filter(ReferenceStaffStatus.source_name == source_name)
        .count()
    )
