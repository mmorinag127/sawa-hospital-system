from datetime import date, timedelta
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.orm import Session, load_only

from .models import CandidateSchedule, CandidateStaffStatus, ScheduleConfirmation, SolveRun, Staff


def _run_end_date(run: SolveRun) -> date | None:
    from .period_metadata_v1 import predecessor_end
    return predecessor_end(run.target_month, run.config_json)


def predecessor_runs(session: Session, next_start_date: date) -> list[SolveRun]:
    confirmed = select(1).where(ScheduleConfirmation.solve_run_id == SolveRun.id).exists()
    return session.query(SolveRun).options(load_only(
        SolveRun.id, SolveRun.target_month, SolveRun.status, SolveRun.started_at, SolveRun.finished_at,
        SolveRun.period_summary_json,
    )).filter(SolveRun.status == "succeeded", SolveRun.period_end_key == next_start_date.isoformat()).order_by(
        case((SolveRun.prior_candidate_start_key == next_start_date.isoformat(), 1), else_=0).desc(),
        confirmed.desc(), SolveRun.id.desc(),
    ).all()


def direct_predecessor_run(session: Session, next_start_date: date) -> SolveRun | None:
    return next(iter(predecessor_runs(session, next_start_date)), None)


def require_direct_predecessor(session: Session, run: SolveRun, next_start_date: date) -> None:
    if run.id not in {row.id for row in predecessor_runs(session, next_start_date)}:
        raise HTTPException(status_code=409, detail={
            "code": "prior_run_not_direct_predecessor",
            "message": "選択したシフトは指定した次期間の直接の前期間ではありません",
            "prior_run_id": run.id,
            "next_period_start_date": next_start_date.isoformat(),
        })


def unresolved_candidate_detail(
    run: SolveRun,
    candidates: list[CandidateSchedule],
    message: str = "前期間に複数の候補があります。使用する候補を確定してください。",
) -> dict:
    config = run.config_json or {}
    return {
        "code": "confirmed_candidate_unresolved",
        "message": message,
        "prior_run_id": run.id,
        "prior_start_date": config.get("start_date"),
        "prior_end_date_exclusive": config.get("end_date"),
        "candidates": [
            {
                "id": row.id,
                "prior_run_id": row.solve_run_id,
                "candidate_no": row.candidate_no,
                "strategy": row.strategy,
                "total_score": row.total_score,
                "label": (row.score_breakdown_json or {}).get("candidate_label"),
            }
            for row in candidates
        ],
    }


def unresolved_predecessor_detail(session: Session, next_start_date: date) -> dict:
    runs = predecessor_runs(session, next_start_date)
    candidates_by_run = {run.id: [] for run in runs}
    if candidates_by_run:
        for candidate in session.query(CandidateSchedule).filter(
            CandidateSchedule.solve_run_id.in_(candidates_by_run),
        ).order_by(CandidateSchedule.candidate_no).all():
            candidates_by_run[candidate.solve_run_id].append(candidate)
    candidates = [candidate for run in runs for candidate in candidates_by_run[run.id]]
    detail = unresolved_candidate_detail(runs[0], candidates)
    detail["message"] = "前期間に複数の履歴または候補があります。使用する案を選んでください。"
    return detail


def confirmed_candidate(session: Session, run: SolveRun) -> CandidateSchedule | None:
    if session.query(ScheduleConfirmation).filter_by(solve_run_id=run.id).first() is None:
        return None
    candidates = session.query(CandidateSchedule).filter_by(solve_run_id=run.id).all()
    recorded_id = (run.config_json or {}).get("confirmed_candidate_id")
    if recorded_id is not None:
        match = next((candidate for candidate in candidates if candidate.id == recorded_id), None)
        if match is not None:
            return match
    elif len(candidates) == 1:
        return candidates[0]
    raise HTTPException(status_code=409, detail=unresolved_candidate_detail(
        run,
        candidates,
        "確定した候補を一意に確認できません。前期間の確定対象を確認してください。",
    ))


def prior_history_candidate(session: Session, run: SolveRun, next_start_date: date) -> CandidateSchedule | None:
    confirmed = confirmed_candidate(session, run)
    if confirmed is not None:
        return confirmed
    config = run.config_json or {}
    selected_id = config.get("prior_candidate_id")
    if selected_id is None:
        return None
    if config.get("prior_candidate_for_start_date") != next_start_date.isoformat():
        raise HTTPException(status_code=409, detail="前期間の参照先の日付が一致しません。候補を選び直してください")
    candidate = session.query(CandidateSchedule).filter_by(id=selected_id, solve_run_id=run.id).one_or_none()
    if candidate is None:
        raise HTTPException(status_code=409, detail="選択済みの前期間候補が見つかりません。候補を選び直してください")
    if config.get("prior_candidate_state_hash") != prior_candidate_state_hash(session, candidate):
        raise HTTPException(status_code=409, detail="前期間の案が選択後に変更されました。候補を選び直してください")
    return candidate


def prior_candidate_state_hash(session: Session, candidate: CandidateSchedule) -> str:
    from .validation_snapshot import candidate_state
    payload = json.dumps(candidate_state(session, candidate), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prior_history_coverage_errors(session: Session, candidate: CandidateSchedule,
                                  next_start_date: date | None = None) -> list[str]:
    if not isinstance((candidate.score_breakdown_json or {}).get("rule_config"), dict):
        return ["前期間の作成設定を確認できません"]
    config = candidate.solve_run.config_json or {}
    try:
        start = date.fromisoformat(config["start_date"])
        end = date.fromisoformat(config["end_date"])
    except (KeyError, TypeError, ValueError):
        return ["前期間の日付を確認できません"]
    if end <= start:
        return ["前期間の日付を確認できません"]
    statuses = session.query(CandidateStaffStatus).filter_by(candidate_id=candidate.id).all()
    recorded = {(row.staff_id, row.work_date) for row in statuses}
    if len(recorded) != len(statuses):
        return ["前期間の勤務・休暇記録が重複しています"]
    staff = session.query(Staff).all()
    day = start
    while day < end:
        if any((row.id, day) not in recorded for row in staff
               if (row.active_from is None or row.active_from <= day)
               and (row.active_to is None or row.active_to >= day)):
            return [f"{day} の勤務・休暇記録が不足しています"]
        day += timedelta(days=1)
    if next_start_date is not None:
        from .candidate_validation import _load_prior_statuses
        from .operational_constraints import load_time_history, required_history_errors
        from .rule_profiles import effective_rule_config
        from .validation_snapshot import historical_time_rows

        settings = effective_rule_config(session)
        prior_status = _load_prior_statuses(config)
        prior_status.update({(row.staff_id, row.work_date): "off" if row.status == "off" else "working"
                             for row in statuses})
        try:
            time_rows = [*(config.get("prior_time_history") or []), *historical_time_rows(session, candidate)]
            prior_time = load_time_history(time_rows, staffs=staff, start=next_start_date, settings=settings)
        except (TypeError, ValueError, KeyError):
            return ["前期間の勤務時間の記録に矛盾または不正な値があります"]
        missing = required_history_errors(staff, next_start_date, settings, prior_status, prior_time)
        if missing:
            first_code, _, first_day = missing[0]
            return [f"{first_day} の{'勤務・休暇' if first_code == 'prior_status_missing' else '勤務枠と時間'}の記録が不足しています"]
    return []
