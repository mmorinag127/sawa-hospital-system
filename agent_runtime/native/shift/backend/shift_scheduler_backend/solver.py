from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from time import monotonic

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from .periods import build_active_settings, resolve_period_request, scale_decimal_units
from .rule_config_utils import (
    PAID_LEAVE_HALF_DAY_WORK_KIND,
    is_allowed_weekday_off,
    is_auto_half_day_allowed,
    is_special_staffing_date as is_special_staffing_date_for_rules,
    day_category,
    day_category_rule,
    is_position_prohibited,
    staff_in_setting_scope,
    saturday_sunday_pairs,
)
from .rotation_queue import resolve_rotation_queue, rotation_period_snapshot, validate_rotation_period
from .models import (
    Assignment,
    AssignmentExplanation,
    Availability,
    CandidateStaffStatus,
    CandidateSchedule,
    Demand,
    Position,
    PositionAssignmentRule,
    PositionPriority,
    ReferenceAssignment,
    ReferenceStaffStatus,
    RotationPolicy,
    RuleConfig,
    ScheduleException,
    SolveRun,
    Staff,
    StaffPairRule,
    StaffPositionPreference,
    StaffPositionRule,
    StaffSkill,
    ShiftTemplate,
    Violation,
    WorkdayRule,
)


def solve_month(
    session: Session,
    target_month: str,
    strategy: str = "balanced",
    locked_assignment_ids: list[int] | None = None,
    candidate_count: int = 1,
) -> SolveRun:
    from .rule_profiles import effective_rule_config
    from .validation_snapshot import begin_solver_input_snapshot
    begin_solver_input_snapshot(session)
    effective_settings = effective_rule_config(session)
    resolved_period = resolve_period_request(
        target_month=target_month,
        start_date=None,
        end_date_exclusive=None,
        settings_config=effective_settings,
    )
    return solve_period(
        session=session,
        label=resolved_period.target_month,
        start_date=resolved_period.start_date,
        end_date=resolved_period.end_date_exclusive,
        strategy=strategy,
        locked_assignment_ids=locked_assignment_ids,
        candidate_count=candidate_count,
        period_mode=resolved_period.period_mode,
        rule_config_override=effective_settings,
        execution_request={"producer": "solve_month", "target_month": target_month,
            "strategy": strategy, "locked_assignment_ids": locked_assignment_ids,
            "candidate_count": candidate_count},
    )


def _demand_min_count(demand: Demand) -> int:
    return demand.min_count if demand.min_count is not None else demand.required_count


def _demand_max_count(demand: Demand) -> int:
    return demand.max_count if demand.max_count is not None else demand.required_count


def _demand_ideal_count(demand: Demand) -> int:
    return demand.ideal_count if demand.ideal_count is not None else demand.required_count


def _demand_target_count(demand: Demand) -> int:
    """Return the count used by deterministic priority and rotation policies."""
    return min(
        max(_demand_ideal_count(demand), _demand_min_count(demand)),
        _demand_max_count(demand),
    )


def _strategy_weights(strategy: str) -> dict[str, int]:
    """Return explicit objective profiles instead of treating every strategy identically."""
    profiles = {
        "preference": {
            "request_off": 5,
            "request_work": 3,
            "coverage": 1,
            "fairness": 1,
            "position_preference": 2,
        },
        "balanced": {
            "request_off": 1,
            "request_work": 1,
            "coverage": 1,
            "fairness": 5,
            "position_preference": 1,
        },
        "fairness": {
            "request_off": 1,
            "request_work": 1,
            "coverage": 1,
            "fairness": 10,
            "position_preference": 1,
        },
        "coverage": {
            "request_off": 2,
            "request_work": 1,
            "coverage": 10,
            "fairness": 2,
            "position_preference": 1,
        },
        "stability": {
            "request_off": 2,
            "request_work": 1,
            "coverage": 2,
            "fairness": 2,
            "position_preference": 2,
        },
    }
    return profiles.get(strategy, profiles["balanced"])


def _is_special_staffing_date(work_date: date, rule_config: dict) -> bool:
    return is_special_staffing_date_for_rules(work_date, rule_config)


def _resolve_weighted_holiday_settings(rule_config: dict) -> dict | None:
    exact_days = rule_config.get("monthly_holiday_exact_days")
    min_days = rule_config.get("monthly_holiday_min_days")
    max_days = rule_config.get("monthly_holiday_max_days")
    if exact_days is None and min_days is None and max_days is None:
        return None
    holiday_count_unit = int(rule_config.get("holiday_count_unit", 2) or 2)
    raw_weights = rule_config.get("holiday_counted_work_kinds") or {}
    working_weight = raw_weights.get("working", 0.0)
    regular_weight = raw_weights.get("regular")
    position_weight = raw_weights.get("position")
    if regular_weight is None:
        regular_weight = working_weight
    if position_weight is None:
        position_weight = working_weight
    return {
        "holiday_count_unit": holiday_count_unit,
        "weights": {
            "off": scale_decimal_units(raw_weights.get("off", 1.0), holiday_count_unit, "holiday_counted_work_kinds.off")
            or 0,
            "half_day": scale_decimal_units(
                raw_weights.get("half_day", 0.5),
                holiday_count_unit,
                "holiday_counted_work_kinds.half_day",
            )
            or 0,
            "regular": scale_decimal_units(
                regular_weight,
                holiday_count_unit,
                "holiday_counted_work_kinds.regular",
            )
            or 0,
            "position": scale_decimal_units(
                position_weight,
                holiday_count_unit,
                "holiday_counted_work_kinds.position",
            )
            or 0,
        },
        "exact_units": scale_decimal_units(exact_days, holiday_count_unit, "monthly_holiday_exact_days"),
        "min_units": scale_decimal_units(min_days, holiday_count_unit, "monthly_holiday_min_days"),
        "max_units": scale_decimal_units(max_days, holiday_count_unit, "monthly_holiday_max_days"),
    }


def _diagnose_infeasible_hard_rule_families(
    model: cp_model.CpModel,
    families: dict[str, set[int]],
) -> list[str]:
    """Return an assumptions-based unsat core without changing the source model."""
    populated = [(name, indexes) for name, indexes in families.items() if indexes]
    deadline = monotonic() + 3
    if not populated:
        return []
    try:
        diagnostic = cp_model.CpModel()
        diagnostic.Proto().copy_from(model.Proto())
        diagnostic.Proto().clear_objective()
        diagnostic.Proto().clear_floating_point_objective()
        supported_by_family = []
        for name, indexes in populated:
            supported_indexes = {
                index for index in indexes
                if _supports_assumption_enforcement(
                    diagnostic.Proto().constraints[index]
                )
            }
            if supported_indexes:
                supported_by_family.append((name, supported_indexes))
        if not supported_by_family:
            return []
        family_by_literal: dict[int, str] = {}
        assumptions = []
        for family_number, (name, indexes) in enumerate(supported_by_family):
            assumption = diagnostic.NewBoolVar(f"diagnostic_family_{family_number}")
            assumptions.append(assumption)
            family_by_literal[assumption.Index()] = name
            for index in indexes:
                diagnostic.Proto().constraints[index].enforcement_literal.append(
                    assumption.Index()
                )
        diagnostic.AddAssumptions(assumptions)
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            return []
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining_seconds
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.stop_after_first_solution = True
        solver.parameters.core_minimization_level = 2
        if solver.Solve(diagnostic) != cp_model.INFEASIBLE or monotonic() >= deadline:
            return []
        core = solver.SufficientAssumptionsForInfeasibility()
        names = [family_by_literal.get(int(literal)) for literal in core]
        return [name for name in names if name is not None]
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return []


def _supports_assumption_enforcement(constraint) -> bool:
    """Match the enforcement support documented by OR-Tools CpModel Constraint."""
    if constraint.has_linear() or constraint.has_bool_or() or constraint.has_bool_and():
        return True
    return constraint.has_interval() and len(constraint.enforcement_literal) == 0


def _add_holiday_equivalent_constraints(
    *,
    model: cp_model.CpModel,
    staffs: list[Staff],
    dates: list[date],
    day_off_vars: dict[tuple[int, date], cp_model.IntVar],
    working_vars: dict[tuple[int, date], cp_model.IntVar],
    assigned_any_by_staff_date: dict[tuple[int, date], cp_model.IntVar],
    variables: dict[tuple[int, int], cp_model.IntVar],
    demand_by_id: dict[int, Demand],
    shift_templates: dict[int, ShiftTemplate],
    workday_rule_by_date: dict[date, WorkdayRule],
    hard_working: dict,
    half_day_overrides: dict[tuple[int, date], int],
    auto_half_day_vars: dict[tuple[int, date], cp_model.IntVar],
    paid_leave_used_by_staff: dict[int, cp_model.IntVar],
    rule_config: dict,
) -> None:
    del hard_working  # Work-kind classification is already represented by the solver variables.
    settings = _resolve_weighted_holiday_settings(rule_config)
    if settings is None:
        return
    weights = settings["weights"]
    paid_leave_units = scale_decimal_units(0.5, settings["holiday_count_unit"], "paid_leave_half_day") or 0
    for staff in staffs:
        if not staff_in_setting_scope(staff.id, rule_config, "monthly_holiday"):
            continue
        terms = []
        active_dates = [
            work_date
            for work_date in dates
            if not (
                (staff.active_from is not None and work_date < staff.active_from)
                or (staff.active_to is not None and work_date > staff.active_to)
            )
        ]
        if not active_dates:
            continue
        for work_date in active_dates:
            key = (staff.id, work_date)
            off = day_off_vars[key]
            working = working_vars[key]
            assigned_any = assigned_any_by_staff_date[key]
            half_assignment_vars = [
                variable
                for (candidate_staff_id, demand_id), variable in variables.items()
                if candidate_staff_id == staff.id
                and demand_by_id[demand_id].work_date == work_date
                and shift_templates[demand_by_id[demand_id].shift_template_id].shift_type == "half_day"
            ]
            half_assigned = sum(half_assignment_vars)
            position_assigned = assigned_any - half_assigned
            unassigned_working = working - assigned_any
            is_unassigned_half_day = _is_half_day_for_staff(
                staff.id,
                work_date,
                workday_rule_by_date,
                half_day_overrides,
            )
            terms.append(off * weights["off"])
            terms.append(half_assigned * weights["half_day"])
            terms.append(position_assigned * weights["position"])
            auto_half_day = auto_half_day_vars.get(key)
            if is_unassigned_half_day:
                terms.append(unassigned_working * weights["half_day"])
            elif auto_half_day is not None:
                terms.append(unassigned_working * weights["regular"])
                terms.append(auto_half_day * (weights["half_day"] - weights["regular"]))
            else:
                terms.append(unassigned_working * weights["regular"])
        total = sum(terms)
        if settings["exact_units"] is not None:
            paid_leave_used = paid_leave_used_by_staff.get(staff.id)
            if paid_leave_used is None:
                model.Add(total == settings["exact_units"])
            else:
                model.Add(total == settings["exact_units"] + paid_leave_units * paid_leave_used)
        if settings["min_units"] is not None:
            model.Add(total >= settings["min_units"])
        if settings["max_units"] is not None:
            model.Add(total <= settings["max_units"])


@dataclass(frozen=True)
class PreparedPeriodSolve:
    session: Session
    solve_run: SolveRun
    start_date: date
    end_date: date
    strategy: str
    reference_source: str | None
    candidate_count: int
    prior_statuses: list[tuple[int, date, str]] | None
    rule_config: dict
    staffs: list[Staff]
    demands: list[Demand]
    workday_rule_by_date: dict[date, WorkdayRule]
    prior_status_map: dict[tuple[int, date], str]
    prior_time_map: dict
    staff_half_day_template_ids: dict[int, int]
    strategy_weights: dict[str, int]
    hard_unavailable: dict[str, set[tuple]]
    soft_request_off: dict
    half_day_overrides: dict
    soft_request_work: dict
    hard_working: dict
    calendar_dates: list[date]
    positions: dict[int, Position]
    shift_templates: dict[int, ShiftTemplate]
    demand_by_id: dict[int, Demand]
    pair_rules: list[StaffPairRule]
    staff_skill_ids: dict[int, set[int]]
    staff_position_penalties: dict[tuple[int, int], int]
    staff_position_rules: dict[str, dict[int, set[int]]]
    planning_rules: dict
    locked_rows: list[Assignment]
    generation_source: dict


class PreparedInputStopped(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(code)
        self.code = code
        self.reason = reason


def solve_period(
    session: Session,
    label: str,
    start_date,
    end_date,
    strategy: str = "balanced",
    reference_source: str | None = None,
    locked_assignment_ids: list[int] | None = None,
    candidate_count: int = 1,
    period_mode: str = "custom",
    prior_statuses: list[tuple[int, date, str]] | None = None,
    prior_time_history: list[dict] | None = None,
    rule_config_override: dict | None = None,
    execution_request: dict | None = None,
) -> SolveRun:
    prepared = prepare_period_solve(
        session, label, start_date, end_date, strategy, reference_source,
        locked_assignment_ids, candidate_count, period_mode, prior_statuses,
        prior_time_history, rule_config_override, execution_request,
    )
    if isinstance(prepared, SolveRun):
        return prepared
    return run_prepared_period_solve(prepared)


def prepare_period_solve(
    session: Session,
    label: str,
    start_date,
    end_date,
    strategy: str = "balanced",
    reference_source: str | None = None,
    locked_assignment_ids: list[int] | None = None,
    candidate_count: int = 1,
    period_mode: str = "custom",
    prior_statuses: list[tuple[int, date, str]] | None = None,
    prior_time_history: list[dict] | None = None,
    rule_config_override: dict | None = None,
    execution_request: dict | None = None,
    *,
    existing_run: SolveRun | None = None,
    commit: bool = True,
) -> PreparedPeriodSolve | SolveRun:
    from .jobs import create_execution_contract, bind_execution_input
    from .validation_snapshot import begin_solver_input_snapshot
    begin_solver_input_snapshot(session)
    start_date = _as_date(start_date)
    end_date = _as_date(end_date)
    if existing_run is not None:
        from .jobs import _execution_json, execution_json_equal
        expected = existing_run.config_json["execution_contract"]["request"]
        supplied = _execution_json({"caller_request": execution_request, "solve_period": {
            "label": label, "start_date": start_date, "end_date_exclusive": end_date,
            "strategy": strategy, "reference_source": reference_source,
            "locked_assignment_ids": locked_assignment_ids, "candidate_count": candidate_count,
            "period_mode": period_mode, "prior_statuses": prior_statuses,
            "prior_time_history": prior_time_history, "rule_config_override": rule_config_override}})
        if commit or not execution_json_equal(expected, supplied):
            raise ValueError("shift_delivery_request_mismatch")
    solve_run = existing_run if existing_run is not None else SolveRun(
        target_month=label,
        status="running",
        started_at=datetime.now(UTC),
        config_json={
            "execution_contract": create_execution_contract({
                "caller_request": execution_request,
                "solve_period": {"label": label, "start_date": start_date,
                    "end_date_exclusive": end_date, "strategy": strategy,
                    "reference_source": reference_source,
                    "locked_assignment_ids": locked_assignment_ids,
                    "candidate_count": candidate_count, "period_mode": period_mode,
                    "prior_statuses": prior_statuses, "prior_time_history": prior_time_history,
                    "rule_config_override": rule_config_override}}),
            "strategy": strategy,
            "start_date": str(start_date),
            "end_date_exclusive": str(end_date),
            "end_date": str(end_date),
            "period_mode": period_mode,
            "reference_source": reference_source,
            "prior_status_count": len(prior_statuses or []),
            "prior_time_history": prior_time_history or [],
            "prior_statuses": [
                {
                    "staff_id": int(staff_id),
                    "work_date": _as_date(work_date).isoformat(),
                    "status": status,
                }
                for staff_id, work_date, status in (prior_statuses or [])
            ],
        },
    )
    if existing_run is None:
        session.add(solve_run)
        session.flush()

    def preparation_failure(message):
        if existing_run is not None:
            raise PreparedInputStopped("shift_delivery_input_unavailable", message)
        if commit:
            return _mark_infeasible(session, solve_run, message)
        return _mark_infeasible(session, solve_run, message, commit=False)

    if rule_config_override is None:
        from .rule_profiles import effective_rule_config
        rule_config = effective_rule_config(session)
    else:
        rule_config = build_active_settings(rule_config_override)
    from .rule_profiles import materialize_required_profile_demands
    try:
        if existing_run is None:
            materialize_required_profile_demands(session, start_date, end_date, rule_config)
    except ValueError as error:
        return preparation_failure(str(error))
    staffs = session.query(Staff).order_by(Staff.id).all()
    demands = (
        session.query(Demand)
        .filter(Demand.work_date >= start_date)
        .filter(Demand.work_date < end_date)
        .order_by(Demand.work_date, Demand.shift_template_id, Demand.position_id)
        .all()
    )
    workday_rules = _load_workday_rules(session, start_date, end_date)
    workday_rule_by_date = {row.work_date: row for row in workday_rules}
    from .validation_snapshot import capture_solver_input_source
    from .operational_constraints import (
        binary_rules, load_time_history, minute_rules, required_history_errors,
        operational_message, slots_conflict, template_slot,
    )
    prior_status_map = {(staff_id, _as_date(day)): status for staff_id, day, status in (prior_statuses or [])}
    try:
        prior_time_map = load_time_history(
            prior_time_history, staffs=staffs, start=start_date, settings=rule_config,
        ) if rule_config.get("require_prior_period_continuity") else {}
    except (ValueError, TypeError, KeyError):
        return preparation_failure(operational_message("prior_time_history_invalid", "勤務表", start_date))
    missing_history = required_history_errors(staffs, start_date, rule_config, prior_status_map, prior_time_map)
    if missing_history:
        staff_names = {staff.id: staff.name for staff in staffs}
        return preparation_failure(" / ".join(operational_message(code, staff_names[staff_id], day) for code, staff_id, day in missing_history))
    staff_half_day_template_ids = {
        int(staff_id): int(template_id)
        for staff_id, template_id in (
            rule_config.get("staff_half_day_shift_template_ids") or {}
        ).items()
    }
    strategy_weights = _strategy_weights(strategy)
    hard_unavailable, soft_request_off, half_day_overrides, soft_request_work, hard_working = _load_availability(
        session, start_date, end_date
    )
    from .operational_constraints import input_calendar_dates, input_calendar_errors
    calendar_dates = input_calendar_dates(session, start_date, end_date)
    calendar_errors = input_calendar_errors(staffs, calendar_dates, start_date, end_date, rule_config)
    if calendar_errors:
        return preparation_failure("; ".join(row["message"] for row in calendar_errors))
    positions = {position.id: position for position in session.query(Position).all()}
    shift_templates = {
        template.id: template for template in session.query(ShiftTemplate).all()
    }
    demand_by_id = {demand.id: demand for demand in demands}
    pair_rules = session.query(StaffPairRule).order_by(StaffPairRule.id).all()
    staff_skill_ids = _load_staff_skill_ids(session)
    staff_position_penalties = _load_staff_position_penalties(session)
    staff_position_rules = _load_staff_position_rules(session)
    try:
        planning_rules = _load_planning_rules(session, start_date, end_date)
    except ValueError as error:
        return preparation_failure(str(error))
    try:
        locked_rows = _load_locked_assignments(session, locked_assignment_ids or [])
    except ValueError as error:
        return preparation_failure(f"固定した担当を読み込めませんでした。{error}")
    generation_source = capture_solver_input_source(
        session, start_date, end_date, effective_settings=rule_config,
        locked_rows=locked_rows, reference_source=reference_source,
        planning_rules=planning_rules, prior_statuses=prior_statuses,
        prior_time_history=prior_time_history,
    )
    if existing_run is not None and "version" not in solve_run.config_json["generation_rule_source"]:
        # Version-one deliveries captured before dated rules existed. Preserve
        # their exact receipt only if the newly introduced relation is empty.
        if generation_source["tables"]["position_assignment_rules"] or generation_source["position_assignment_rules_revision"] != 1:
            raise PreparedInputStopped("shift_delivery_input_changed", "Dated rules were authored after input capture")
        del generation_source["version"]
        del generation_source["tables"]["position_assignment_rules"]
        del generation_source["position_assignment_rules_revision"]
    prepared_config = {
        **solve_run.config_json,
        "effective_settings": rule_config,
        "generation_rule_source": generation_source,
        "rotation_state_snapshot": planning_rules["rotation_state_snapshot"],
        "rotation_queue_snapshot": planning_rules["rotation_queue_snapshot"],
        "input_snapshot": {
            "demand_ids": [demand.id for demand in demands],
            "workday_rule_ids": [row.id for row in workday_rules],
            "exception_ids": [exception.id for exception in planning_rules["exceptions"]],
            "position_assignment_rule_ids": [rule.id for rule in planning_rules["position_assignment_rules"]],
            "rotation_policy_ids": [policy.id for policy in planning_rules["rotation_policies"]],
            "staff_pair_rule_ids": [rule.id for rule in pair_rules],
            "locked_assignment_ids": [row.id for row in locked_rows],
            "locked_assignments": [
                {"staff_id": row.staff_id, "work_date": row.work_date.isoformat(),
                 "position_id": row.position_id, "shift_template_id": row.shift_template_id}
                for row in locked_rows
            ],
        },
    }

    if existing_run is None:
        solve_run.config_json = prepared_config
        bind_execution_input(solve_run)
    else:
        from .jobs import execution_json_equal, recorded_input_version
        if (not execution_json_equal(generation_source, solve_run.config_json.get("generation_rule_source"))
                or recorded_input_version(prepared_config) != solve_run.config_json["execution_contract"]["inputVersion"]):
            raise PreparedInputStopped("shift_delivery_input_changed", "Recorded canonical inputs differ")
        generation_source = solve_run.config_json["generation_rule_source"]
    return PreparedPeriodSolve(
        session=session, solve_run=solve_run, start_date=start_date, end_date=end_date,
        strategy=strategy, reference_source=reference_source, candidate_count=candidate_count,
        prior_statuses=prior_statuses, rule_config=rule_config, staffs=staffs,
        demands=demands, workday_rule_by_date=workday_rule_by_date,
        prior_status_map=prior_status_map, prior_time_map=prior_time_map,
        staff_half_day_template_ids=staff_half_day_template_ids,
        strategy_weights=strategy_weights, hard_unavailable=hard_unavailable,
        soft_request_off=soft_request_off, half_day_overrides=half_day_overrides,
        soft_request_work=soft_request_work, hard_working=hard_working,
        calendar_dates=calendar_dates, positions=positions, shift_templates=shift_templates,
        demand_by_id=demand_by_id, pair_rules=pair_rules, staff_skill_ids=staff_skill_ids,
        staff_position_penalties=staff_position_penalties,
        staff_position_rules=staff_position_rules, planning_rules=planning_rules,
        locked_rows=locked_rows,
        generation_source=generation_source,
    )


def run_prepared_period_solve(prepared: PreparedPeriodSolve, *, commit: bool = True) -> SolveRun:
    from .operational_constraints import binary_rules, minute_rules, slots_conflict, template_slot
    mark_failure = _mark_infeasible if commit else partial(_mark_infeasible, commit=False)

    session = prepared.session
    solve_run = prepared.solve_run
    start_date = prepared.start_date
    end_date = prepared.end_date
    strategy = prepared.strategy
    reference_source = prepared.reference_source
    candidate_count = prepared.candidate_count
    prior_statuses = prepared.prior_statuses
    rule_config = prepared.rule_config
    staffs = prepared.staffs
    demands = prepared.demands
    workday_rule_by_date = prepared.workday_rule_by_date
    prior_status_map = prepared.prior_status_map
    prior_time_map = prepared.prior_time_map
    staff_half_day_template_ids = prepared.staff_half_day_template_ids
    strategy_weights = prepared.strategy_weights
    hard_unavailable = prepared.hard_unavailable
    soft_request_off = prepared.soft_request_off
    half_day_overrides = prepared.half_day_overrides
    soft_request_work = prepared.soft_request_work
    hard_working = prepared.hard_working
    calendar_dates = prepared.calendar_dates
    positions = prepared.positions
    shift_templates = prepared.shift_templates
    demand_by_id = prepared.demand_by_id
    pair_rules = prepared.pair_rules
    staff_skill_ids = prepared.staff_skill_ids
    staff_position_penalties = prepared.staff_position_penalties
    staff_position_rules = prepared.staff_position_rules
    planning_rules = prepared.planning_rules
    locked_rows = prepared.locked_rows
    generation_source = prepared.generation_source
    model = cp_model.CpModel()
    hard_rule_families: dict[str, set[int]] = {}

    def record_hard_rule_family(name: str, first_constraint: int) -> None:
        indexes = set(range(first_constraint, len(model.Proto().constraints)))
        if indexes:
            hard_rule_families.setdefault(name, set()).update(indexes)

    variables: dict[tuple[int, int], cp_model.IntVar] = {}
    member_staff_ids = {int(value) for value in rule_config.get("member_staff_ids") or []}

    explicit_assignment_keys = {
        (row.staff_id, row.work_date, row.position_id)
        for row in locked_rows
    } | {
        (row.staff_id, row.work_date, row.position_id)
        for row in planning_rules["exceptions"]
        if row.kind == "fixed_assignment" and row.staff_id is not None and row.position_id is not None
    }
    no_auto_position_ids = {
        int(value) for value in rule_config.get("auto_assignment_prohibited_position_ids") or []
    }

    family_start = len(model.Proto().constraints)
    for demand in demands:
        position = positions[demand.position_id]
        if is_position_prohibited(demand.position_id, demand.work_date, rule_config):
            if _demand_min_count(demand) > 0:
                return mark_failure(
                    session, solve_run,
                    f"{demand.work_date} は {position.display_name or position.name} を配置できません",
                )
            continue
        for staff in staffs:
            key = (staff.id, demand.id)
            if (
                demand.work_date.weekday() == 6
                and staff.id in member_staff_ids
                and shift_templates[demand.shift_template_id].shift_type != "half_day"
            ):
                continue
            if _assignment_eligibility_error(
                staff, demand, position, hard_unavailable, staff_skill_ids,
                staff_position_rules, workday_rule_by_date, shift_templates,
                rule_config, half_day_overrides,
            ):
                continue
            variables[key] = model.NewBoolVar(f"x_s{staff.id}_d{demand.id}")
            if (
                demand.position_id in no_auto_position_ids
                and (staff.id, demand.work_date, demand.position_id) not in explicit_assignment_keys
            ):
                model.Add(variables[key] == 0)

    for demand in demands:
        demand_vars = [variables[(staff.id, demand.id)] for staff in staffs if (staff.id, demand.id) in variables]
        position = positions[demand.position_id]
        for group in rule_config.get("position_staffing_groups") or []:
            if group["position_id"] == demand.position_id:
                demand_present = _assignment_presence(model, demand_vars, f"lead_group_d{demand.id}")
                model.Add(sum(
                    variables[(staff_id, demand.id)]
                    for staff_id in set(group["staff_ids"])
                    if (staff_id, demand.id) in variables
                ) >= group["min_count"]).OnlyEnforceIf(demand_present)
        minimum = _demand_min_count(demand)
        maximum = _demand_max_count(demand)
        ideal = _demand_ideal_count(demand)
        if minimum > maximum or ideal < minimum or ideal > maximum:
            return mark_failure(
                session,
                solve_run,
                f"{demand.work_date} の必要人数の最小値・目安・最大値の順序が正しくありません",
            )
        if len(demand_vars) < minimum:
            return mark_failure(
                session,
                solve_run,
                f"{demand.work_date} の担当枠は、入れる人が {len(demand_vars)} 人で必要人数 {minimum} 人に足りません",
            )
        model.Add(sum(demand_vars) >= minimum)
        model.Add(sum(demand_vars) <= min(maximum, len(demand_vars)))
        for required_staff_id in position.required_staff_ids or []:
            required_variable = variables.get((required_staff_id, demand.id))
            if required_variable is None:
                return mark_failure(
                    session, solve_run, f"{demand.work_date} の {position.name} に必須担当者を配置できません",
                )
            else:
                model.Add(required_variable == 1)

    demands_by_date = defaultdict(list)
    for demand in demands:
        demands_by_date[demand.work_date].append(demand)
    for work_date in calendar_dates:
        demands_by_date.setdefault(work_date, [])

    for staff in staffs:
        for work_date, day_demands in demands_by_date.items():
            staff_day_vars = [
                variables[(staff.id, demand.id)]
                for demand in day_demands
                if (staff.id, demand.id) in variables
            ]
            if staff_day_vars:
                daily_limit = _daily_work_limit_minutes(
                    staff=staff,
                    workday_rule=workday_rule_by_date.get(work_date),
                    shift_templates=shift_templates,
                    rule_config=rule_config,
                    override_shift_template_id=half_day_overrides.get((staff.id, work_date)),
                )
                model.Add(
                    sum(
                        _shift_minutes(shift_templates[demand_by_id[demand_id].shift_template_id])
                        * variable
                        for (staff_id, demand_id), variable in variables.items()
                        if staff_id == staff.id
                        and demand_by_id[demand_id].work_date == work_date
                    )
                    <= daily_limit
                )

        min_rest_minutes = int(rule_config.get("min_rest_minutes", 0) or 0)
        if demands:
            staff_demands = sorted(
                demands,
                key=lambda demand: (
                    demand.work_date,
                    _shift_start_minutes(shift_templates[demand.shift_template_id]),
                    demand.id,
                ),
            )
            for first_demand, second_demand in combinations(staff_demands, 2):
                first_variable = variables.get((staff.id, first_demand.id))
                second_variable = variables.get((staff.id, second_demand.id))
                if first_variable is None or second_variable is None:
                    continue
                if _shifts_require_rest(
                    first_demand.work_date,
                    shift_templates[first_demand.shift_template_id],
                    second_demand.work_date,
                    shift_templates[second_demand.shift_template_id],
                    min_rest_minutes,
                ):
                    model.Add(first_variable + second_variable <= 1)

    from .operational_constraints import active_on
    staff_by_id = {staff.id: staff for staff in staffs}
    for staff_id, work_date, shift_template_id in hard_working["shifts"]:
        if staff_id in staff_by_id and not active_on(staff_by_id[staff_id], work_date):
            continue
        requested_shift_vars = [
            variable
            for (candidate_staff_id, demand_id), variable in variables.items()
            if candidate_staff_id == staff_id
            and demand_by_id[demand_id].work_date == work_date
            and demand_by_id[demand_id].shift_template_id == shift_template_id
        ]
        if not requested_shift_vars:
            return mark_failure(
                session,
                solve_run,
                f"{work_date}: 希望勤務の時間帯に入れる枠がありません",
            )
        model.Add(sum(requested_shift_vars) >= 1)

    penalties = []
    soft_request_penalty_terms = []
    position_preference_penalty_terms = []
    consecutive_work_penalty_terms = []
    target_work_minutes_penalty_terms = []
    for (staff_id, work_date, shift_template_id), weight in soft_request_off.items():
        for demand in demands_by_date.get(work_date, []):
            if shift_template_id is not None and demand.shift_template_id != shift_template_id:
                continue
            variable = variables.get((staff_id, demand.id))
            if variable is not None:
                term = variable * weight * strategy_weights["request_off"]
                penalties.append(term)
                soft_request_penalty_terms.append(term)

    coverage_weight = strategy_weights["coverage"] if rule_config.get("coverage_weight") is None else int(rule_config["coverage_weight"])
    if coverage_weight:
        for demand in demands:
            minimum = _demand_min_count(demand)
            maximum = _demand_max_count(demand)
            ideal = min(max(_demand_ideal_count(demand), minimum), maximum)
            if minimum == maximum == ideal:
                continue
            demand_vars = [
                variables[(staff.id, demand.id)]
                for staff in staffs
                if (staff.id, demand.id) in variables
            ]
            actual_count = sum(demand_vars)
            deviation = model.NewIntVar(0, max(len(demand_vars), ideal), f"demand_deviation_{demand.id}")
            model.AddAbsEquality(deviation, actual_count - ideal)
            penalties.append(deviation * coverage_weight)

    for (staff_id, demand_id), variable in variables.items():
        demand = next(item for item in demands if item.id == demand_id)
        penalty = staff_position_penalties.get((staff_id, demand.position_id), 0)
        if penalty:
            term = variable * penalty
            penalties.append(term)
            position_preference_penalty_terms.append(term)

    _add_preferred_position_penalties(
        variables=variables,
        demands=demands,
        priorities=planning_rules["position_priorities"],
        penalties=penalties,
        penalty_terms=position_preference_penalty_terms,
        scale=strategy_weights["position_preference"],
    )

    deterministic_errors, next_rotation_queues, deterministic_reasons = _prepare_assignment_locks(
        model, demands, variables, planning_rules, locked_rows,
    )
    record_hard_rule_family("担当枠・担当者条件", family_start)
    if deterministic_errors:
        return mark_failure(session, solve_run, "; ".join(deterministic_errors))

    ordinary_templates = {}
    for staff in staffs:
        for work_date in calendar_dates:
            workday_rule = workday_rule_by_date.get(work_date)
            preferred_half_day_template_id = staff_half_day_template_ids.get(staff.id)
            preferred_half_day = shift_templates.get(preferred_half_day_template_id)
            workday_half_day_template_id = None
            if (
                workday_rule is not None
                and workday_rule.kind == "half_day"
                and staff.id not in (workday_rule.full_day_staff_ids or [])
                and preferred_half_day is not None
                and preferred_half_day.shift_type == "half_day"
            ):
                workday_half_day_template_id = preferred_half_day_template_id
            template_id = half_day_overrides.get((staff.id, work_date))
            if template_id is None and workday_rule is not None:
                if workday_rule.kind == "half_day" and staff.id not in (workday_rule.full_day_staff_ids or []):
                    template_id = workday_half_day_template_id or workday_rule.shift_template_id
                else:
                    template_id = workday_rule.full_day_shift_template_id or workday_rule.shift_template_id
            ordinary_templates[(staff.id, work_date)] = shift_templates.get(template_id)

    _day_off_vars, working_vars = _add_day_off_rules(
        model=model,
        staffs=staffs,
        demands_by_date=demands_by_date,
        variables=variables,
        hard_unavailable=hard_unavailable,
        hard_working=hard_working,
        half_day_overrides=half_day_overrides,
        pair_rules=pair_rules,
        rule_config=rule_config,
        penalties=penalties,
        ordinary_templates=ordinary_templates,
        calendar_dates=calendar_dates,
        workday_rule_by_date=workday_rule_by_date,
        closed_dates={
            work_date
            for work_date, row in workday_rule_by_date.items()
            if row.kind == "closed"
        },
        default_work_dates={
            work_date
            for work_date, row in workday_rule_by_date.items()
            if row.kind in {"normal", "half_day"}
        },
        half_day_shift_template_ids={
            template_id
            for template_id, template in shift_templates.items()
            if template.shift_type == "half_day"
        },
        prior_statuses=prior_statuses or [],
        period_start_date=start_date,
        period_end_date_exclusive=end_date,
        hard_rule_families=hard_rule_families,
    )
    family_start = len(model.Proto().constraints)
    continuity_error = _add_prior_period_continuity_rules(
        model=model,
        staffs=staffs,
        start_date=start_date,
        current_dates=calendar_dates,
        day_off_vars=_day_off_vars,
        rule_config=rule_config,
        prior_statuses=prior_statuses or [],
    )
    record_hard_rule_family("前期間を含む連続勤務条件", family_start)
    if continuity_error:
        return mark_failure(session, solve_run, continuity_error)

    family_start = len(model.Proto().constraints)
    weekday_no_off_ids = {int(value) for value in rule_config.get("weekday_no_off_staff_ids") or []}
    for staff_id in weekday_no_off_ids:
        for work_date in calendar_dates:
            if day_category(work_date, rule_config) == "weekday" and (staff_id, work_date) in working_vars:
                model.Add(working_vars[(staff_id, work_date)] == 1)

    for row in rule_config.get("weekday_position_assignments") or []:
        staff_id, position_id = int(row["staff_id"]), int(row["position_id"])
        for work_date in calendar_dates:
            if day_category(work_date, rule_config) != "weekday" or (staff_id, work_date) not in working_vars:
                continue
            matching = [
                variables[(staff_id, demand.id)]
                for demand in demands_by_date[work_date]
                if demand.position_id == position_id and (staff_id, demand.id) in variables
            ]
            if not matching:
                return mark_failure(session, solve_run, f"{work_date} の固定担当枠がありません")
            model.Add(working_vars[(staff_id, work_date)] == 1)
            model.Add(sum(matching) >= 1)

    for first_id, second_id in rule_config.get("off_overlap_pairs") or []:
        for work_date in calendar_dates:
            if (int(first_id), work_date) in _day_off_vars and (int(second_id), work_date) in _day_off_vars:
                model.Add(_day_off_vars[(int(first_id), work_date)] + _day_off_vars[(int(second_id), work_date)] <= 1)
    record_hard_rule_family("profile固定勤務・同時休み禁止条件", family_start)

    sunday_dates = [work_date for work_date in calendar_dates if work_date.weekday() == 6]
    sunday_target = rule_config.get("sunday_target_work_count")
    if sunday_target is not None:
        weight = int(rule_config.get("sunday_target_work_weight", 100) or 100)
        for staff in staffs:
            if member_staff_ids and staff.id not in member_staff_ids:
                continue
            count = sum(working_vars[(staff.id, day)] for day in sunday_dates if (staff.id, day) in working_vars)
            deviation = model.NewIntVar(0, len(sunday_dates), f"sunday_target_gap_s{staff.id}")
            model.AddAbsEquality(deviation, count - int(sunday_target))
            squared_deviation = model.NewIntVar(
                0, len(sunday_dates) ** 2, f"sunday_target_squared_gap_s{staff.id}"
            )
            model.AddMultiplicationEquality(squared_deviation, [deviation, deviation])
            penalties.append(squared_deviation * weight)

    balance_groups = rule_config.get("weekend_balance_groups") or []
    if len(balance_groups) == 2:
        first_ids, second_ids = ({int(value) for value in group} for group in balance_groups)
        weekend_dates = [day for day in calendar_dates if day.weekday() >= 5]
        first_total = sum(working_vars[(staff_id, day)] for staff_id in first_ids for day in weekend_dates if (staff_id, day) in working_vars)
        second_total = sum(working_vars[(staff_id, day)] for staff_id in second_ids for day in weekend_dates if (staff_id, day) in working_vars)
        gap = model.NewIntVar(0, len(weekend_dates) * len(first_ids) * len(second_ids), "weekend_group_ratio_gap")
        model.AddAbsEquality(gap, first_total * len(second_ids) - second_total * len(first_ids))
        penalties.append(gap * int(rule_config.get("weekend_group_balance_weight", 100) or 100))
    soft_request_work_penalty_terms = []
    paid_leave_penalty_terms = []
    paid_leave_used_by_staff: dict[int, cp_model.IntVar] = {}
    exact_holiday_settings = _resolve_weighted_holiday_settings(rule_config)
    paid_leave_penalty_weight = 100000
    if exact_holiday_settings is not None and exact_holiday_settings["exact_units"] is not None:
        for staff in staffs:
            if not staff_in_setting_scope(staff.id, rule_config, "monthly_holiday"):
                continue
            if not staff.paid_leave_eligible:
                continue
            paid_leave_used = model.NewBoolVar(f"paid_leave_used_s{staff.id}")
            paid_leave_used_by_staff[staff.id] = paid_leave_used
            term = paid_leave_used * paid_leave_penalty_weight
            penalties.append(term)
            paid_leave_penalty_terms.append(term)

    consecutive_work_weight = int(rule_config.get("consecutive_work_weight", 0) or 0)
    if consecutive_work_weight:
        for staff in staffs:
            staff_dates = sorted(
                work_date
                for work_date in calendar_dates
                if (staff.id, work_date) in working_vars
            )
            for first_date, second_date in zip(staff_dates, staff_dates[1:], strict=False):
                if (second_date - first_date).days != 1:
                    continue
                consecutive = model.NewBoolVar(
                    f"consecutive_work_s{staff.id}_{first_date}"
                )
                first_working = working_vars[(staff.id, first_date)]
                second_working = working_vars[(staff.id, second_date)]
                model.AddBoolAnd([first_working, second_working]).OnlyEnforceIf(consecutive)
                model.AddBoolOr(
                    [first_working.Not(), second_working.Not()]
                ).OnlyEnforceIf(consecutive.Not())
                term = consecutive * consecutive_work_weight
                penalties.append(term)
                consecutive_work_penalty_terms.append(term)

    work_minutes_by_staff_date: dict[tuple[int, date], cp_model.IntVar | int] = {}
    assigned_any_by_staff_date: dict[tuple[int, date], cp_model.IntVar] = {}
    auto_half_day_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    for staff in staffs:
        for work_date in calendar_dates:
            day_demands = demands_by_date[work_date]
            staff_day_vars = [
                variables[(staff.id, demand.id)]
                for demand in day_demands
                if (staff.id, demand.id) in variables
            ]
            assigned_minutes = sum(
                _shift_minutes(shift_templates[demand.shift_template_id]) * variable
                for demand, variable in (
                    (demand, variables[(staff.id, demand.id)])
                    for demand in day_demands
                    if (staff.id, demand.id) in variables
                )
            )
            workday_rule = workday_rule_by_date.get(work_date)
            preferred_half_day_template_id = staff_half_day_template_ids.get(staff.id)
            preferred_half_day = shift_templates.get(preferred_half_day_template_id)
            base_template = ordinary_templates[(staff.id, work_date)]
            base_minutes = _shift_minutes(base_template) if base_template is not None else 0
            auto_half_day = None
            if (
                preferred_half_day is not None
                and preferred_half_day.shift_type == "half_day"
                and workday_rule is not None
                and workday_rule.kind == "normal"
                and is_auto_half_day_allowed(work_date, rule_config)
                and (staff.id, work_date) not in half_day_overrides
                and (staff.id, work_date) not in hard_working["days"]
            ):
                auto_half_day = model.NewBoolVar(
                    f"auto_half_day_s{staff.id}_{work_date}"
                )
                model.Add(auto_half_day <= working_vars[(staff.id, work_date)])
                auto_half_day_vars[(staff.id, work_date)] = auto_half_day
                if (
                    work_date.weekday() == 6
                    and "sunday_half_day" in set(rule_config.get("profile_scoped_setting_keys") or [])
                    and staff_in_setting_scope(staff.id, rule_config, "sunday_half_day")
                ):
                    model.Add(auto_half_day == working_vars[(staff.id, work_date)])
            if not staff_day_vars:
                assigned_any = model.NewBoolVar(f"assigned_any_s{staff.id}_{work_date}")
                model.Add(assigned_any == 0)
                assigned_any_by_staff_date[(staff.id, work_date)] = assigned_any
                if auto_half_day is None:
                    work_minutes_by_staff_date[(staff.id, work_date)] = (
                        base_minutes * working_vars[(staff.id, work_date)]
                    )
                else:
                    half_minutes = _shift_minutes(preferred_half_day)
                    work_minutes_by_staff_date[(staff.id, work_date)] = (
                        base_minutes * working_vars[(staff.id, work_date)]
                        + (half_minutes - base_minutes) * auto_half_day
                    )
                continue
            assigned_any = model.NewBoolVar(f"assigned_any_s{staff.id}_{work_date}")
            model.Add(sum(staff_day_vars) >= assigned_any)
            model.Add(sum(staff_day_vars) <= len(staff_day_vars) * assigned_any)
            assigned_any_by_staff_date[(staff.id, work_date)] = assigned_any
            unassigned_working = model.NewBoolVar(
                f"unassigned_working_s{staff.id}_{work_date}"
            )
            working = working_vars[(staff.id, work_date)]
            model.Add(unassigned_working <= working)
            model.Add(unassigned_working + assigned_any <= 1)
            model.Add(unassigned_working >= working - assigned_any)
            if auto_half_day is not None:
                model.Add(auto_half_day <= unassigned_working)
            daily_minutes = model.NewIntVar(0, 24 * 60, f"work_minutes_s{staff.id}_{work_date}")
            half_day_adjustment = (
                (_shift_minutes(preferred_half_day) - base_minutes) * auto_half_day
                if auto_half_day is not None
                else 0
            )
            model.Add(
                daily_minutes
                == assigned_minutes + base_minutes * unassigned_working + half_day_adjustment
            )
            work_minutes_by_staff_date[(staff.id, work_date)] = daily_minutes

    family_start = len(model.Proto().constraints)
    _add_holiday_equivalent_constraints(
        model=model,
        staffs=staffs,
        dates=calendar_dates,
        day_off_vars=_day_off_vars,
        working_vars=working_vars,
        assigned_any_by_staff_date=assigned_any_by_staff_date,
        variables=variables,
        demand_by_id=demand_by_id,
        shift_templates=shift_templates,
        workday_rule_by_date=workday_rule_by_date,
        hard_working=hard_working,
        half_day_overrides=half_day_overrides,
        auto_half_day_vars=auto_half_day_vars,
        paid_leave_used_by_staff=paid_leave_used_by_staff,
        rule_config=rule_config,
    )
    record_hard_rule_family("休日換算日数条件", family_start)

    for (staff_id, work_date, shift_template_id), weight in soft_request_work.items():
        if shift_template_id is None:
            present = working_vars.get((staff_id, work_date))
        else:
            requested_shift_vars = [
                variable
                for (candidate_staff_id, demand_id), variable in variables.items()
                if candidate_staff_id == staff_id
                and demand_by_id[demand_id].work_date == work_date
                and demand_by_id[demand_id].shift_template_id == shift_template_id
            ]
            present = _assignment_presence(
                model,
                requested_shift_vars,
                f"request_work_s{staff_id}_{work_date}_{shift_template_id}",
            )
        if present is not None:
            term = (1 - present) * weight * strategy_weights["request_work"]
            penalties.append(term)
            soft_request_work_penalty_terms.append(term)

    try:
        operational_rules = list(minute_rules(staffs, calendar_dates, rule_config, work_minutes_by_staff_date, prior_time_map))
        operational_rules.extend(binary_rules(staffs, calendar_dates, rule_config, _day_off_vars, prior_status_map))
    except ValueError as error:
        return mark_failure(session, solve_run, str(error))
    family_start = len(model.Proto().constraints)
    for _, _, _, expression, relation, bound in operational_rules:
        model.Add(expression <= bound if relation == "le" else expression >= bound if relation == "ge" else expression == bound)
    record_hard_rule_family("勤務時間・休息条件", family_start)

    for staff in staffs:
        slots = []
        for (staff_id, demand_id), variable in variables.items():
            if staff_id == staff.id:
                demand = demand_by_id[demand_id]
                slots.append((demand.work_date, template_slot(shift_templates[demand.shift_template_id]), variable))
        for day in calendar_dates:
            key = (staff.id, day)
            auto_half = auto_half_day_vars.get(key, 0)
            ordinary = working_vars[key] - assigned_any_by_staff_date[key] - auto_half
            template = ordinary_templates[key]
            if template is None:
                model.Add(ordinary == 0)
            else:
                slots.append((day, template_slot(template), ordinary))
            if key in auto_half_day_vars:
                slots.append((day, template_slot(shift_templates[staff_half_day_template_ids[staff.id]]), auto_half))
            model.Add(work_minutes_by_staff_date[key] <= _daily_work_limit_minutes(
                staff, workday_rule_by_date.get(day), shift_templates, rule_config,
                half_day_overrides.get(key),
            ))
        rest = int(rule_config.get("min_rest_minutes") or 0)
        for day, slot, present in slots:
            if _is_hard_unavailable(staff.id, day, slot["shift_template_id"], hard_unavailable):
                model.Add(present == 0)
        for first, second in combinations(slots, 2):
            if slots_conflict(first[0], first[1], second[0], second[1], rest):
                model.Add(first[2] + second[2] <= 1)
        for (staff_id, prior_day), row in prior_time_map.items():
            if staff_id != staff.id or prior_day >= start_date:
                continue
            for prior_slot in row.get("slots") or []:
                for day, slot, present in slots:
                    if slots_conflict(prior_day, prior_slot, day, slot, rest):
                        model.Add(present == 0)
    record_hard_rule_family("担当時間帯・勤務間隔条件", family_start)

    target_work_minutes_weight = int(rule_config.get("target_work_minutes_weight", 0) or 0)
    if target_work_minutes_weight and calendar_dates:
        max_period_minutes = len(calendar_dates) * 24 * 60
        for staff in staffs:
            target_minutes = staff.target_monthly_work_minutes
            if target_minutes is None:
                continue
            total_work_minutes = sum(
                work_minutes_by_staff_date[(staff.id, work_date)]
                for work_date in calendar_dates
            )
            target_deviation = model.NewIntVar(
                0,
                max_period_minutes,
                f"target_work_minutes_deviation_s{staff.id}",
            )
            model.AddAbsEquality(target_deviation, total_work_minutes - int(target_minutes))
            term = target_deviation * target_work_minutes_weight
            penalties.append(term)
            target_work_minutes_penalty_terms.append(term)

    max_workload_value = sum(
        _demand_max_count(demand) * max(1, positions[demand.position_id].difficulty_weight)
        for demand in demands
    )
    workloads = []
    for staff in staffs:
        staff_terms = [
            max(1, positions[demand_by_id[demand_id].position_id].difficulty_weight) * variable
            for (staff_id, demand_id), variable in variables.items()
            if staff_id == staff.id
        ]
        workload = model.NewIntVar(0, max_workload_value, f"workload_s{staff.id}")
        if staff_terms:
            model.Add(workload == sum(staff_terms))
        else:
            model.Add(workload == 0)
        workloads.append(workload)

    max_workload = model.NewIntVar(0, max_workload_value, "max_workload")
    min_workload = model.NewIntVar(0, max_workload_value, "min_workload")
    model.AddMaxEquality(max_workload, workloads)
    model.AddMinEquality(min_workload, workloads)
    fairness_gap = model.NewIntVar(0, max_workload_value, "fairness_gap")
    model.Add(fairness_gap == max_workload - min_workload)
    fairness_weight = strategy_weights["fairness"]
    penalties.append(fairness_gap * fairness_weight)

    reference_vars = []
    reference_status_vars = []
    missing_reference_count = 0
    if reference_source:
        reference_rows = (
            session.query(ReferenceAssignment)
            .filter(ReferenceAssignment.source_name == reference_source)
            .filter(ReferenceAssignment.confidence.in_(["high", "medium"]))
            .filter(ReferenceAssignment.work_date >= start_date)
            .filter(ReferenceAssignment.work_date < end_date)
            .all()
        )
        for row in reference_rows:
            matching_demands = [
                demand
                for demand in demands_by_date.get(row.work_date, [])
                if demand.shift_template_id == row.shift_template_id
                and demand.position_id == row.position_id
            ]
            if not matching_demands:
                missing_reference_count += 1
                continue
            variable = variables.get((row.staff_id, matching_demands[0].id))
            if variable is None:
                missing_reference_count += 1
                continue
            mismatch = model.NewBoolVar(f"ref_mismatch_{row.id}")
            model.Add(variable == 0).OnlyEnforceIf(mismatch)
            model.Add(variable == 1).OnlyEnforceIf(mismatch.Not())
            reference_vars.append(mismatch)
        if reference_vars:
            penalties.append(sum(reference_vars) * 1000)

        reference_status_rows = (
            session.query(ReferenceStaffStatus)
            .filter(ReferenceStaffStatus.source_name == reference_source)
            .filter(ReferenceStaffStatus.confidence.in_(["high", "medium"]))
            .filter(ReferenceStaffStatus.work_date >= start_date)
            .filter(ReferenceStaffStatus.work_date < end_date)
            .all()
        )
        for row in reference_status_rows:
            if row.work_kind == "position":
                continue
            working = working_vars.get((row.staff_id, row.work_date))
            assigned_any = assigned_any_by_staff_date.get((row.staff_id, row.work_date))
            if working is None or assigned_any is None:
                missing_reference_count += 1
                continue
            if row.status == "off":
                mismatch = working
            else:
                is_half_day = _is_half_day_for_staff(
                    row.staff_id,
                    row.work_date,
                    workday_rule_by_date,
                    half_day_overrides,
                )
                actual_kind = model.NewBoolVar(
                    f"reference_actual_{row.id}_{row.work_kind}"
                )
                expected_half_day = row.work_kind == "half_day"
                auto_half_day = auto_half_day_vars.get((row.staff_id, row.work_date))
                if is_half_day == expected_half_day:
                    model.Add(actual_kind == working)
                    model.Add(actual_kind <= assigned_any.Not())
                elif auto_half_day is not None and expected_half_day:
                    model.Add(actual_kind == auto_half_day)
                elif auto_half_day is not None:
                    model.Add(actual_kind == working - assigned_any - auto_half_day)
                else:
                    model.Add(actual_kind == 0)
                mismatch = model.NewBoolVar(f"reference_status_mismatch_{row.id}")
                model.Add(mismatch == 1 - actual_kind)
            reference_status_vars.append(mismatch)
        if reference_status_vars:
            penalties.append(sum(reference_status_vars) * 1000)

    shift_fairness_weight = int(rule_config.get("shift_fairness_weight", 0) or 0)
    if shift_fairness_weight:
        configured_shift_types = rule_config.get("balanced_shift_types")
        shift_types = set(configured_shift_types or [])
        if not shift_types:
            shift_types = {
                shift_templates[demand.shift_template_id].shift_type
                for demand in demands
                if shift_templates[demand.shift_template_id].shift_type != "regular"
            }
        for shift_type in sorted(shift_types):
            shift_counts = []
            for staff in staffs:
                count = model.NewIntVar(0, len(demands), f"shift_type_{shift_type}_s{staff.id}")
                model.Add(
                    count
                    == sum(
                        variable
                        for (staff_id, demand_id), variable in variables.items()
                        if staff_id == staff.id
                        and shift_templates[demand_by_id[demand_id].shift_template_id].shift_type
                        == shift_type
                    )
                )
                shift_counts.append(count)
            max_shift_count = model.NewIntVar(0, len(demands), f"max_shift_{shift_type}")
            min_shift_count = model.NewIntVar(0, len(demands), f"min_shift_{shift_type}")
            model.AddMaxEquality(max_shift_count, shift_counts)
            model.AddMinEquality(min_shift_count, shift_counts)
            shift_gap = model.NewIntVar(0, len(demands), f"shift_gap_{shift_type}")
            model.Add(shift_gap == max_shift_count - min_shift_count)
            penalties.append(shift_gap * shift_fairness_weight)

    # Keep equal-scored solutions reproducible. The scale makes this a
    # secondary table-order tie-break and prevents it from trading away a
    # primary rule such as fairness or a request-off penalty.
    stable_terms = [staff_id * variable for (staff_id, _demand_id), variable in variables.items()]
    stable_bound = sum(staff_id for staff_id, _demand_id in variables)
    date_order = {work_date: index + 1 for index, work_date in enumerate(calendar_dates)}
    for (staff_id, work_date), variable in auto_half_day_vars.items():
        coefficient = staff_id * (len(calendar_dates) + 1) + date_order[work_date]
        stable_terms.append(coefficient * variable)
        stable_bound += coefficient
    for (staff_id, work_date), variable in _day_off_vars.items():
        coefficient = staff_id * (len(calendar_dates) + 1) + date_order[work_date]
        stable_terms.append(coefficient * variable)
        stable_bound += coefficient
    for staff_id, variable in paid_leave_used_by_staff.items():
        # Keep the secondary objective bounded. Multiplying by the accumulated
        # bound grows exponentially for real-world staff IDs and can overflow
        # CP-SAT's integer objective domain.
        coefficient = staff_id
        stable_terms.append(coefficient * variable)
        stable_bound += coefficient
    primary_objective = sum(penalties)
    model.Minimize(primary_objective * (stable_bound + 1) + sum(stable_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    effective_candidate_count = max(1, min(int(candidate_count), 5))
    candidate_results: list[dict] = []
    for candidate_no in range(1, effective_candidate_count + 1):
        status = solver.Solve(model)
        solve_run.solver_status = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break

        values = {key: solver.Value(variable) for key, variable in variables.items()}
        candidate_results.append(
            {
                "candidate_no": candidate_no,
                "values": values,
                "working_values": {
                    key: solver.Value(variable) for key, variable in working_vars.items()
                },
                "auto_half_day_values": {
                    key: solver.Value(variable)
                    for key, variable in auto_half_day_vars.items()
                },
                "paid_leave_used_by_staff": {
                    staff_id: solver.Value(variable)
                    for staff_id, variable in paid_leave_used_by_staff.items()
                },
                "objective": solver.ObjectiveValue(),
                "fairness_gap": solver.Value(fairness_gap),
                "reference_mismatches": sum(solver.Value(var) for var in reference_vars),
                "reference_status_mismatches": sum(
                    solver.Value(var) for var in reference_status_vars
                ),
                "solver_status": solver.StatusName(status),
            }
        )

        # Exclude the complete assignment so subsequent candidates are genuinely
        # different, while retaining the same hard rules and score objective.
        if candidate_no < effective_candidate_count and variables:
            same_assignment = sum(
                variable if values[key] else 1 - variable
                for key, variable in variables.items()
            )
            model.Add(same_assignment <= len(variables) - 1)

    if not candidate_results:
        message = "条件をすべて満たすシフト案を作成できませんでした。"
        if solve_run.solver_status == "INFEASIBLE":
            details = _diagnose_infeasible_hard_rule_families(model, hard_rule_families)
            if details:
                message += f" 競合する可能性が高い条件: {'、'.join(details)}。"
            else:
                message += " 競合する条件族を特定できませんでした。"
        else:
            message += " 設定を確認してください。"
        return mark_failure(session, solve_run, message)
    solve_run.solver_status = candidate_results[0]["solver_status"]

    preferred_priorities = {
        (priority.staff_id, priority.position_id): priority
        for priority in planning_rules["position_priorities"]
        if priority.mode == "prefer"
    }

    locked_keys = {
        (row.staff_id, row.work_date, row.shift_template_id, row.position_id)
        for row in locked_rows
    }
    candidate_savepoint = session.begin_nested()
    for result in candidate_results:
        candidate = CandidateSchedule(
            solve_run_id=solve_run.id,
            candidate_no=result["candidate_no"],
            strategy=strategy,
            total_score=round(result["objective"]),
            score_breakdown_json={
                "objective": result["objective"],
                "fairness_gap": result["fairness_gap"],
                "fairness_metric": "position_difficulty_weighted_workload",
                "strategy_weights": strategy_weights,
                "tie_break": "staff_id_order_after_primary_objective",
                "soft_request_off_penalty_terms": len(soft_request_penalty_terms),
                "soft_request_work_penalty_terms": len(soft_request_work_penalty_terms),
                "position_preference_penalty_terms": len(position_preference_penalty_terms),
                "consecutive_work_penalty_terms": len(consecutive_work_penalty_terms),
                "target_work_minutes_penalty_terms": len(target_work_minutes_penalty_terms),
                "paid_leave_penalty_terms": len(paid_leave_penalty_terms),
                "rule_config": rule_config,
                "reference_source": reference_source,
                "reference_assignments_checked": len(reference_vars),
                    "reference_mismatches": result["reference_mismatches"],
                    "reference_statuses_checked": len(reference_status_vars),
                    "reference_status_mismatches": result["reference_status_mismatches"],
                "reference_assignments_unmatchable": missing_reference_count,
                "next_rotation_queues": next_rotation_queues,
                "deterministic_assignment_snapshot": [
                    {"staff_id": staff_id, "demand_id": demand_id, "category": reason[0]}
                    for (staff_id, demand_id), reason in sorted(deterministic_reasons.items())
                ],
                "candidate_label": "基本案" if result["candidate_no"] == 1 else f"別案 {result['candidate_no'] - 1}",
            },
        )
        session.add(candidate)
        session.flush()

        selected_demand_by_staff_date = {
            (staff_id, demand_by_id[demand_id].work_date): demand_by_id[demand_id]
            for (staff_id, demand_id), value in result["values"].items()
            if value == 1
        }
        for staff in staffs:
            staff_status_rows: list[CandidateStaffStatus] = []
            if staff.active_from and staff.active_from > end_date:
                continue
            if staff.active_to and staff.active_to < start_date:
                continue
            for work_date in calendar_dates:
                workday_rule = workday_rule_by_date.get(work_date)
                if staff.active_from and work_date < staff.active_from:
                    continue
                if staff.active_to and work_date > staff.active_to:
                    continue
                demand = selected_demand_by_staff_date.get((staff.id, work_date))
                if demand is not None:
                    status = "working"
                    work_kind = "position"
                    shift_template_id = demand.shift_template_id
                elif result["working_values"][(staff.id, work_date)] == 1:
                    status = "working"
                    is_global_half_day = (
                        workday_rule is not None and workday_rule.kind == "half_day"
                        and staff.id not in (workday_rule.full_day_staff_ids or [])
                    )
                    is_auto_half_day = (
                        result["auto_half_day_values"].get((staff.id, work_date), 0) == 1
                    )
                    is_half_day = is_global_half_day or is_auto_half_day or (
                        (staff.id, work_date) in half_day_overrides
                    )
                    work_kind = "half_day" if is_half_day else "regular"
                    if (staff.id, work_date) in half_day_overrides:
                        shift_template_id = half_day_overrides[(staff.id, work_date)]
                    elif is_global_half_day:
                        shift_template_id = (
                            staff_half_day_template_ids.get(staff.id)
                            or workday_rule.shift_template_id
                        )
                    elif is_auto_half_day:
                        shift_template_id = staff_half_day_template_ids.get(staff.id)
                    else:
                        template = ordinary_templates[(staff.id, work_date)]
                        shift_template_id = template.id if template is not None else None
                else:
                    status = "off"
                    work_kind = "off"
                    shift_template_id = None
                status_row = CandidateStaffStatus(
                    candidate_id=candidate.id,
                    staff_id=staff.id,
                    work_date=work_date,
                    status=status,
                    work_kind=work_kind,
                    shift_template_id=shift_template_id,
                )
                session.add(status_row)
                staff_status_rows.append(status_row)
            if result["paid_leave_used_by_staff"].get(staff.id, 0) == 1:
                paid_leave_row = next(
                    (
                        row
                        for row in staff_status_rows
                        if row.status == "working" and row.work_kind == "half_day"
                    ),
                    None,
                )
                if paid_leave_row is not None:
                    paid_leave_row.work_kind = PAID_LEAVE_HALF_DAY_WORK_KIND

        for (staff_id, demand_id), value in result["values"].items():
            if value != 1:
                continue
            demand = demand_by_id[demand_id]
            reason = deterministic_reasons.get((staff_id, demand_id))
            if reason is None:
                priority = preferred_priorities.get((staff_id, demand.position_id))
                if priority is not None:
                    reason = (
                        "preference",
                        f"持ち場の優先順（第{priority.rank}位）を考慮して選ばれました",
                    )
            assignment = Assignment(
                candidate_id=candidate.id,
                staff_id=staff_id,
                work_date=demand.work_date,
                shift_template_id=demand.shift_template_id,
                position_id=demand.position_id,
                is_locked=(
                    staff_id,
                    demand.work_date,
                    demand.shift_template_id,
                    demand.position_id,
                ) in locked_keys,
                source=reason[0] if reason else "solver",
            )
            session.add(assignment)
            session.flush()
            if reason:
                session.add(
                    AssignmentExplanation(
                        assignment_id=assignment.id,
                        category=reason[0],
                        message=reason[1],
                    )
                )
            request_off_weight = _soft_request_weight(
                soft_request_off,
                staff_id,
                demand.work_date,
                demand.shift_template_id,
            )
            if request_off_weight is not None:
                session.add(
                    Violation(
                        candidate_id=candidate.id,
                        solve_run_id=solve_run.id,
                        severity="soft",
                        rule_name="SOFT_REQUEST_OFF",
                        staff_id=staff_id,
                        work_date=demand.work_date,
                        message="希望休として登録された日に勤務を割り当てました。",
                        penalty=request_off_weight,
                    )
                )

    from .operational_constraints import validate_operational
    from .assignment_validation import validate_assignments
    from .operational_constraints import candidate_time_rows
    from .candidate_validation import validate_candidate
    from .validation_snapshot import candidate_state
    session.flush()
    period_dates = calendar_dates
    for saved_candidate in session.query(CandidateSchedule).filter_by(solve_run_id=solve_run.id).all():
        violations = validate_operational(
            session, saved_candidate, staffs, period_dates, rule_config,
            prior_status_map, workday_rule_by_date,
        )
        violations.extend(validate_assignments(session, saved_candidate, start_date, end_date, rule_config))
        violations = [row for row in violations if row["severity"] == "error"]
        if violations:
            candidate_savepoint.rollback()
            return mark_failure(session, solve_run, "; ".join(row["message"] for row in violations))
        saved_candidate.score_breakdown_json = {
            **saved_candidate.score_breakdown_json,
            "generation_time_snapshot": {
                "rule_source": generation_source,
                "candidate_state": candidate_state(session, saved_candidate),
                "validation": (validate_candidate(saved_candidate.id, session)
                    if commit else validate_candidate(saved_candidate.id, session, settings_override=rule_config)),
                "time_rows": candidate_time_rows(session, saved_candidate.id),
            },
        }
    candidate_savepoint.commit()
    solve_run.status = "succeeded"
    solve_run.finished_at = datetime.now(UTC)
    from .jobs import record_execution_terminal
    record_execution_terminal(solve_run)
    if commit:
        session.commit()
    return solve_run


def _shift_minutes(template: ShiftTemplate) -> int:
    return max(0, _shift_end_minutes(template) - _shift_start_minutes(template) - template.break_minutes)


def _shift_start_minutes(template: ShiftTemplate) -> int:
    return template.start_time.hour * 60 + template.start_time.minute


def _shift_end_minutes(template: ShiftTemplate) -> int:
    start = _shift_start_minutes(template)
    end = template.end_time.hour * 60 + template.end_time.minute
    if end <= start:
        end += 24 * 60
    return end


def _shifts_overlap(first: ShiftTemplate, second: ShiftTemplate) -> bool:
    return _shifts_require_rest(date.min, first, date.min, second, 0)


def _shifts_require_rest(
    first_date: date,
    first: ShiftTemplate,
    second_date: date,
    second: ShiftTemplate,
    minimum_rest_minutes: int,
) -> bool:
    first_start = first_date.toordinal() * 24 * 60 + _shift_start_minutes(first)
    first_end = first_date.toordinal() * 24 * 60 + _shift_end_minutes(first)
    second_start = second_date.toordinal() * 24 * 60 + _shift_start_minutes(second)
    second_end = second_date.toordinal() * 24 * 60 + _shift_end_minutes(second)
    if first_start > second_start:
        first_start, second_start = second_start, first_start
        first_end, second_end = second_end, first_end
    return second_start - first_end < minimum_rest_minutes


def _daily_work_limit_minutes(
    staff: Staff,
    workday_rule: WorkdayRule | None,
    shift_templates: dict[int, ShiftTemplate],
    rule_config: dict,
    override_shift_template_id: int | None = None,
) -> int:
    configured_limit = rule_config.get("max_daily_work_minutes")
    staff_limit = staff.max_daily_work_minutes
    configured_limits = [limit for limit in (configured_limit, staff_limit) if limit is not None]
    default_limit = min((int(limit) for limit in configured_limits), default=24 * 60)
    if override_shift_template_id is not None:
        return min(default_limit, _shift_minutes(shift_templates[override_shift_template_id]))
    if (workday_rule is not None and workday_rule.kind == "half_day"):
        if staff.id not in (workday_rule.full_day_staff_ids or []):
            if workday_rule.shift_template_id is not None:
                return min(default_limit, _shift_minutes(shift_templates[workday_rule.shift_template_id]))
            return min(default_limit, int(rule_config.get("half_day_work_minutes", 4 * 60)))
        full_day_template_id = workday_rule.full_day_shift_template_id
        if full_day_template_id is not None:
            return min(default_limit, _shift_minutes(shift_templates[full_day_template_id]))
        return min(default_limit, int(rule_config.get("full_day_work_minutes", 8 * 60)))
    if workday_rule is None:
        return default_limit
    if workday_rule.kind == "closed":
        return 0
    if workday_rule.shift_template_id is not None:
        return min(default_limit, _shift_minutes(shift_templates[workday_rule.shift_template_id]))
    return default_limit


def _load_staff_position_rules(session: Session) -> dict[str, dict[int, set[int]]]:
    allowed_by_position: dict[int, set[int]] = defaultdict(set)
    denied: set[tuple[int, int]] = set()
    for row in session.query(StaffPositionRule).all():
        if row.kind == "allow":
            allowed_by_position[row.position_id].add(row.staff_id)
        else:
            denied.add((row.staff_id, row.position_id))
    return {"allowed_by_position": allowed_by_position, "denied": denied}


def _position_is_allowed(staff_id: int, position_id: int, rules: dict) -> bool:
    if (staff_id, position_id) in rules.get("denied", set()):
        return False
    allowed = rules.get("allowed_by_position", {}).get(position_id)
    return not allowed or staff_id in allowed


def _assignment_presence(
    model: cp_model.CpModel,
    variables: list[cp_model.IntVar],
    name: str,
) -> cp_model.IntVar:
    present = model.NewBoolVar(name)
    if not variables:
        model.Add(present == 0)
        return present
    model.Add(sum(variables) >= present)
    model.Add(sum(variables) <= len(variables) * present)
    return present


def _is_half_day_for_staff(
    staff_id: int,
    work_date: date,
    workday_rule_by_date: dict[date, WorkdayRule],
    half_day_overrides: dict[tuple[int, date], int | None],
) -> bool:
    if (staff_id, work_date) in half_day_overrides:
        return True
    rule = workday_rule_by_date.get(work_date)
    return bool(
        rule
        and rule.kind == "half_day"
        and staff_id not in (rule.full_day_staff_ids or [])
    )


def _load_staff_skill_ids(session: Session) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for row in session.query(StaffSkill).all():
        result[row.staff_id].add(row.skill_id)
    return result


def _load_locked_assignments(session: Session, assignment_ids: list[int]) -> list[Assignment]:
    if not assignment_ids:
        return []
    unique_ids = sorted(set(assignment_ids))
    rows = session.query(Assignment).filter(Assignment.id.in_(unique_ids)).all()
    if len(rows) != len(unique_ids):
        missing = sorted(set(unique_ids) - {row.id for row in rows})
        raise ValueError(f"locked assignments not found: {missing}")
    candidate_ids = {row.candidate_id for row in rows}
    if len(candidate_ids) != 1:
        raise ValueError("locked assignments must belong to one candidate")
    return rows


def _assignment_eligibility_error(staff, demand, position, hard_unavailable, staff_skill_ids,
                                  staff_position_rules, workday_rules, templates, settings, half_day_overrides):
    if ((staff.active_from and demand.work_date < staff.active_from)
            or (staff.active_to and demand.work_date > staff.active_to)):
        return "在籍期間外の担当です"
    if is_position_prohibited(position.id, demand.work_date, settings):
        return "この曜日区分には配置できない持ち場です"
    if _is_hard_unavailable(staff.id, demand.work_date, demand.shift_template_id, hard_unavailable):
        return "勤務不可または必須の希望休に担当が配置されています"
    if position.required_skill_id and position.required_skill_id not in staff_skill_ids[staff.id]:
        return "持ち場に必要なスキルが登録されていません"
    if not _position_is_allowed(staff.id, position.id, staff_position_rules):
        return "担当可能者の設定により、この持ち場には配置できません"
    workday = workday_rules.get(demand.work_date)
    if workday is not None and workday.kind == "closed":
        return "休業日に担当が配置されています"
    limit = _daily_work_limit_minutes(staff, workday, templates, settings,
                                     half_day_overrides.get((staff.id, demand.work_date)))
    if limit <= 0 or _shift_minutes(templates[demand.shift_template_id]) > limit:
        return "この勤務枠は1日の勤務時間上限を満たしません"
    return None


def _prepare_assignment_locks(model, demands, variables, planning_rules, locked_rows):
    reasons = {}
    errors = _add_fixed_assignment_rules(model, demands, variables, planning_rules["exceptions"], reasons, locked_rows)
    errors.extend(_add_position_assignment_rules(model, demands, variables, planning_rules["position_assignment_rules"], reasons))
    demand_by_id = {demand.id: demand for demand in demands}
    reserved = {(staff_id, demand_by_id[demand_id].work_date) for staff_id, demand_id in reasons}
    errors.extend(_add_strict_position_priority_rules(
        model, demands, variables, planning_rules["position_priorities"], reserved, reasons,
    ))
    rotation_errors, next_queues = _add_rotation_rules(
        model, demands, variables, planning_rules["rotation_policies"], planning_rules["starting_queues"],
        planning_rules["exceptions"], reserved, reasons,
    )
    errors.extend(rotation_errors)
    for demand in demands:
        assigned = {staff_id for staff_id, demand_id in reasons if demand_id == demand.id}
        if any(category == "position_rule" for (staff_id, demand_id), (category, _) in reasons.items() if demand_id == demand.id) and len(assigned) > _demand_target_count(demand):
            errors.append(f"{demand.work_date}: {demand.position.name} の期間付き担当と優先順・交代順が衝突しています")
    return errors, next_queues, reasons


def _load_staff_position_penalties(session: Session) -> dict[tuple[int, int], int]:
    return {
        (row.staff_id, row.position_id): row.penalty
        for row in session.query(StaffPositionPreference).all()
    }




def _load_workday_rules(session: Session, start_date: date, end_date: date) -> list[WorkdayRule]:
    return (
        session.query(WorkdayRule)
        .filter(WorkdayRule.work_date >= start_date)
        .filter(WorkdayRule.work_date < end_date)
        .order_by(WorkdayRule.work_date, WorkdayRule.id)
        .all()
    )


def _load_planning_rules(session: Session, start_date: date, end_date: date) -> dict:
    policies = (
        session.query(RotationPolicy)
        .filter(RotationPolicy.enabled.is_(True))
        .order_by(RotationPolicy.id)
        .all()
    )
    starting_queues: dict[int, list[int]] = {}
    rotation_state_snapshot: dict[str, int | None] = {}
    errors = validate_rotation_period(session, policies, start_date, end_date)
    if errors:
        raise ValueError(" / ".join(errors))
    for policy in policies:
        state, starting_queue = resolve_rotation_queue(session, policy, start_date)
        starting_queues[policy.id] = starting_queue
        rotation_state_snapshot[str(policy.id)] = state.id if state else None

    exceptions = (
        session.query(ScheduleException)
        .filter(ScheduleException.work_date >= start_date)
        .filter(ScheduleException.work_date < end_date)
        .order_by(ScheduleException.work_date, ScheduleException.id)
        .all()
    )
    priorities = session.query(PositionPriority).order_by(PositionPriority.position_id, PositionPriority.rank).all()
    return {
        "rotation_policies": policies,
        "starting_queues": starting_queues,
        "rotation_state_snapshot": rotation_state_snapshot,
        "rotation_queue_snapshot": rotation_period_snapshot(session, policies, start_date, end_date),
        "exceptions": exceptions,
        "position_assignment_rules": session.query(PositionAssignmentRule).filter(
            PositionAssignmentRule.effective_from < end_date,
            PositionAssignmentRule.effective_to.is_(None) | (PositionAssignmentRule.effective_to >= start_date),
        ).order_by(PositionAssignmentRule.position_id, PositionAssignmentRule.effective_from, PositionAssignmentRule.id).all(),
        "position_priorities": priorities,
    }


def dated_assignment_preflight(session, start, end, settings):
    rules = session.query(PositionAssignmentRule).filter(
        PositionAssignmentRule.effective_from < end,
        PositionAssignmentRule.effective_to.is_(None) | (PositionAssignmentRule.effective_to >= start),
    ).all()
    if not rules:
        return []
    planning = _load_planning_rules(session, start, end)
    demands = session.query(Demand).filter(Demand.work_date >= start, Demand.work_date < end).order_by(
        Demand.work_date, Demand.shift_template_id, Demand.position_id,
    ).all()
    positions = {row.id: row for row in session.query(Position).all()}
    templates = {row.id: row for row in session.query(ShiftTemplate).all()}
    workdays = {row.work_date: row for row in _load_workday_rules(session, start, end)}
    hard, _, half_days, _, _ = _load_availability(session, start, end)
    skills = _load_staff_skill_ids(session)
    staff_rules = _load_staff_position_rules(session)
    values = {(staff.id, demand.id): 1 for staff in session.query(Staff).all() for demand in demands
              if _assignment_eligibility_error(staff, demand, positions[demand.position_id], hard,
                  skills, staff_rules, workdays, templates, settings, half_days) is None}

    from .assignment_validation import AssignmentChecks
    errors, _, _ = _prepare_assignment_locks(AssignmentChecks(), demands, values, planning, [])
    return errors


def _add_position_assignment_rules(model, demands, variables, rules, reasons):
    errors = []
    for demand in demands:
        matching = [row for row in rules if row.position_id == demand.position_id
                    and (row.shift_template_id is None or row.shift_template_id == demand.shift_template_id)
                    and row.effective_from <= demand.work_date
                    and (row.effective_to is None or demand.work_date <= row.effective_to)]
        if not matching:
            continue
        if len(matching) != 1:
            errors.append(f"{demand.work_date}: {demand.position.name} の期間付き担当設定が重複しています")
            continue
        rule = matching[0]
        existing = {staff_id for staff_id, demand_id in reasons if demand_id == demand.id}
        if existing and existing != {rule.staff_id}:
            errors.append(f"{demand.work_date}: {demand.position.name} の固定担当設定が重複しています")
            continue
        if _demand_target_count(demand) < 1:
            errors.append(f"{demand.work_date}: {demand.position.name} の期間付き担当設定が必要人数を超えています")
            continue
        variable = variables.get((rule.staff_id, demand.id))
        if variable is None:
            errors.append(f"{demand.work_date}: {demand.position.name} の期間付き担当者は勤務できないか、必要なスキルがありません")
            continue
        model.Add(variable == 1)
        if (rule.staff_id, demand.id) not in reasons:
            reasons[(rule.staff_id, demand.id)] = ("position_rule", rule.reason)
    return errors


def _add_fixed_assignment_rules(
    model: cp_model.CpModel,
    demands: list[Demand],
    variables: dict[tuple[int, int], cp_model.IntVar],
    exceptions: list[ScheduleException],
    reasons: dict[tuple[int, int], tuple[str, str]],
    locked_rows: list[Assignment] | None = None,
) -> list[str]:
    errors = []
    for exception in (item for item in exceptions if item.kind == "fixed_assignment"):
        if exception.staff_id is None or exception.position_id is None:
            errors.append(f"{exception.work_date}: 固定担当にスタッフまたは持ち場がありません")
            continue
        matching_demands = [
            demand
            for demand in demands
            if demand.work_date == exception.work_date
            and demand.position_id == exception.position_id
            and (
                exception.shift_template_id is None
                or demand.shift_template_id == exception.shift_template_id
            )
        ]
        if len(matching_demands) != 1:
            errors.append(f"{exception.work_date}: 固定担当の対象枠を特定できません")
            continue
        demand = matching_demands[0]
        variable = variables.get((exception.staff_id, demand.id))
        if variable is None:
            errors.append(f"{exception.work_date}: 固定担当者はこの持ち場に入れません")
            continue
        model.Add(variable == 1)
        reasons[(exception.staff_id, demand.id)] = ("fixed", exception.reason)

    for locked in locked_rows or []:
        matching_demands = [
            demand
            for demand in demands
            if demand.work_date == locked.work_date
            and demand.shift_template_id == locked.shift_template_id
            and demand.position_id == locked.position_id
        ]
        if len(matching_demands) != 1:
            errors.append(f"{locked.work_date}: 固定した担当の対象枠を特定できません")
            continue
        demand = matching_demands[0]
        variable = variables.get((locked.staff_id, demand.id))
        if variable is None:
            errors.append(f"{locked.work_date}: 固定した人員はこの持ち場に入れません")
            continue
        model.Add(variable == 1)
        reasons[(locked.staff_id, demand.id)] = ("locked", "前回のシフトで固定した担当です")
    return errors


def _add_rotation_rules(
    model: cp_model.CpModel,
    demands: list[Demand],
    variables: dict[tuple[int, int], cp_model.IntVar],
    policies: list[RotationPolicy],
    starting_queues: dict[int, list[int]],
    exceptions: list[ScheduleException],
    reserved_staff_by_date: set[tuple[int, date]],
    reasons: dict[tuple[int, int], tuple[str, str]],
) -> tuple[list[str], dict[str, list[int]]]:
    errors = []
    next_queues: dict[str, list[int]] = {}
    exceptions_by_policy_date: dict[tuple[int, date], list[ScheduleException]] = defaultdict(list)
    for exception in exceptions:
        if exception.rotation_policy_id is not None and exception.kind in {
            "rotation_force",
            "rotation_exclusion",
        }:
            exceptions_by_policy_date[(exception.rotation_policy_id, exception.work_date)].append(exception)

    for policy in policies:
        queue = list(starting_queues.get(policy.id, []))
        if not queue or len(queue) != len(set(queue)):
            errors.append(f"{policy.name}: 交代順が設定されていません")
            continue
        policy_demands = sorted(
            (demand for demand in demands if demand.position_id == policy.position_id),
            key=lambda demand: (demand.work_date, demand.id),
        )
        for demand in policy_demands:
            day_exceptions = exceptions_by_policy_date.get((policy.id, demand.work_date), [])
            forced = [item for item in day_exceptions if item.kind == "rotation_force"]
            excluded_staff_ids = {
                item.staff_id for item in day_exceptions if item.kind == "rotation_exclusion" and item.staff_id
            }
            if len(forced) > 1:
                errors.append(f"{demand.work_date}: {policy.name} の前倒し担当が複数あります")
                continue
            if forced and forced[0].staff_id in excluded_staff_ids:
                errors.append(f"{demand.work_date}: {policy.name} の前倒しと除外が衝突しています")
                continue

            selected_staff_ids: list[int] = []
            if forced:
                forced_staff_id = forced[0].staff_id
                if forced_staff_id is None or forced_staff_id not in queue:
                    errors.append(f"{demand.work_date}: {policy.name} の前倒し担当が交代順にいません")
                    continue
                if not _rotation_candidate_can_be_used(
                    forced_staff_id,
                    demand,
                    demands,
                    variables,
                ):
                    errors.append(f"{demand.work_date}: {policy.name} の前倒し担当が他の固定担当と重なっています")
                    continue
                selected_staff_ids.append(forced_staff_id)

            for staff_id in queue:
                if len(selected_staff_ids) >= _demand_target_count(demand):
                    break
                if staff_id in selected_staff_ids or staff_id in excluded_staff_ids:
                    continue
                if (staff_id, demand.work_date) in reserved_staff_by_date:
                    continue
                if not _rotation_candidate_can_be_used(
                    staff_id,
                    demand,
                    demands,
                    variables,
                ):
                    continue
                if variables.get((staff_id, demand.id)) is not None:
                    selected_staff_ids.append(staff_id)

            if len(selected_staff_ids) != _demand_target_count(demand):
                errors.append(f"{demand.work_date}: {policy.name} に入れる人が足りません")
                continue
            for staff_id in selected_staff_ids:
                variable = variables.get((staff_id, demand.id))
                if variable is None:
                    errors.append(f"{demand.work_date}: {policy.name} の指定担当は勤務できません")
                    continue
                model.Add(variable == 1)
                matching_exception = next(
                    (item for item in day_exceptions if item.staff_id == staff_id), None
                )
                message = (
                    matching_exception.reason
                    if matching_exception is not None
                    else f"{policy.name} の交代順で次の担当です"
                )
                reasons[(staff_id, demand.id)] = ("rotation", message)
                reserved_staff_by_date.add((staff_id, demand.work_date))
                queue.remove(staff_id)
                queue.append(staff_id)
        next_queues[str(policy.id)] = queue
    return errors, next_queues


def _rotation_candidate_can_be_used(
    staff_id: int,
    rotation_demand: Demand,
    demands: list[Demand],
    variables: dict[tuple[int, int], cp_model.IntVar],
) -> bool:
    rotation_template = rotation_demand.shift_template
    for other_demand in demands:
        if other_demand.id == rotation_demand.id or other_demand.work_date != rotation_demand.work_date:
            continue
        if not _shifts_overlap(rotation_template, other_demand.shift_template):
            continue
        if staff_id in (other_demand.position.required_staff_ids or []):
            return False
        other_vars = [
            variable
            for (candidate_staff_id, demand_id), variable in variables.items()
            if demand_id == other_demand.id
        ]
        if staff_id in {
            candidate_staff_id
            for (candidate_staff_id, demand_id) in variables
            if demand_id == other_demand.id
        } and len(other_vars) <= _demand_min_count(other_demand):
            return False
    return True


def _add_strict_position_priority_rules(
    model: cp_model.CpModel,
    demands: list[Demand],
    variables: dict[tuple[int, int], cp_model.IntVar],
    priorities: list[PositionPriority],
    reserved_staff_by_date: set[tuple[int, date]],
    reasons: dict[tuple[int, int], tuple[str, str]],
) -> list[str]:
    strict_by_position: dict[int, list[PositionPriority]] = defaultdict(list)
    for priority in priorities:
        if priority.mode == "strict":
            strict_by_position[priority.position_id].append(priority)

    for demand in demands:
        rules = strict_by_position.get(demand.position_id, [])
        if not rules:
            continue
        selected = []
        for rule in rules:
            if (rule.staff_id, demand.work_date) in reserved_staff_by_date:
                continue
            variable = variables.get((rule.staff_id, demand.id))
            if variable is None:
                continue
            selected.append((rule, variable))
            if len(selected) == _demand_target_count(demand):
                break
        for rule, variable in selected:
            model.Add(variable == 1)
            reasons[(rule.staff_id, demand.id)] = ("priority", "持ち場の優先順で選ばれました")
            reserved_staff_by_date.add((rule.staff_id, demand.work_date))
    return []


def _add_preferred_position_penalties(
    variables: dict[tuple[int, int], cp_model.IntVar],
    demands: list[Demand],
    priorities: list[PositionPriority],
    penalties: list,
    penalty_terms: list,
    scale: int = 1,
) -> None:
    preferred_by_position: dict[int, list[PositionPriority]] = defaultdict(list)
    for priority in priorities:
        if priority.mode == "prefer":
            preferred_by_position[priority.position_id].append(priority)
    demand_by_id = {demand.id: demand for demand in demands}
    for position_id, rules in preferred_by_position.items():
        by_staff_id = {rule.staff_id: rule for rule in rules}
        fallback_penalty = (len(rules) + 1) * 10
        for (staff_id, demand_id), variable in variables.items():
            demand = demand_by_id[demand_id]
            if demand.position_id != position_id:
                continue
            rule = by_staff_id.get(staff_id)
            penalty = (rule.rank - 1) * 10 if rule else fallback_penalty
            if penalty:
                term = variable * penalty * scale
                penalties.append(term)
                penalty_terms.append(term)


def _add_prior_period_continuity_rules(
    *,
    model: cp_model.CpModel,
    staffs: list[Staff],
    start_date: date,
    current_dates: list[date],
    day_off_vars: dict[tuple[int, date], cp_model.IntVar],
    rule_config: dict,
    prior_statuses: list[tuple[int, date, str]],
) -> str | None:
    if start_date.weekday() == 6:
        saturday = start_date - timedelta(days=1)
        prior_keys = {
            (int(staff_id), _as_date(work_date))
            for staff_id, work_date, status in prior_statuses
            if status in {"off", "working"}
        }
        weekend_staff_ids = {
            int(row["staff_id"]) for row in rule_config.get("staff_weekend_rules", [])
            if int(row.get("minimum_weekend_off_days", 0) or 0) > 0
        }
        for staff in staffs:
            if (
                staff.id in weekend_staff_ids
                and (staff.active_from is None or staff.active_from <= saturday)
                and (staff.active_to is None or staff.active_to >= start_date)
                and (staff.id, saturday) not in prior_keys
            ):
                return f"{staff.name} の週末休みを確認するため、{saturday} の勤務実績が必要です"
    if not prior_statuses:
        has_boundary_staff = any(
            (staff.active_from is None or staff.active_from < start_date)
            and (staff.active_to is None or staff.active_to >= start_date)
            and (
                not rule_config.get("require_prior_period_continuity")
                or staff_in_setting_scope(staff.id, rule_config, "max_consecutive_work_days")
                or bool(rule_config.get("weekly_min_off_days"))
            )
            for staff in staffs
        )
        if (rule_config.get("require_prior_period_continuity") or rule_config.get("max_consecutive_off_days")) and has_boundary_staff:
            return "前期間末尾の勤務実績が必要です"
        return None
    continuity = bool(rule_config.get("require_prior_period_continuity"))
    weekly_minimum = int(rule_config.get("weekly_min_off_days", 0) or 0) if continuity else 0
    max_consecutive = int(rule_config.get("max_consecutive_work_days", 0) or 0) if continuity else 0
    max_consecutive_off = int(rule_config.get("max_consecutive_off_days", 0) or 0)
    window_sizes = []
    if weekly_minimum:
        window_sizes.append(7)
    if max_consecutive:
        window_sizes.append(max_consecutive + 1)
    if max_consecutive_off:
        window_sizes.append(max_consecutive_off + 1)
    if not window_sizes:
        return None
    history_days = max(window_sizes) - 1
    expected_prior_dates = [
        start_date - timedelta(days=offset) for offset in range(history_days, 0, -1)
    ]
    expected_date_set = set(expected_prior_dates)
    prior_by_key: dict[tuple[int, date], str] = {}
    for staff_id, work_date, status in prior_statuses:
        if work_date >= start_date:
            return f"前期間実績の日付 {work_date} は必要範囲外です"
        if work_date not in expected_date_set:
            continue
        if status not in {"working", "off"}:
            return f"前期間実績の状態 {status} は使用できません"
        key = (int(staff_id), work_date)
        if key in prior_by_key and prior_by_key[key] != status:
            return f"前期間実績が重複しています: staff_id={staff_id}, date={work_date}"
        prior_by_key[key] = status

    contiguous_current_dates = [
        start_date + timedelta(days=offset) for offset in range(min(history_days, len(current_dates)))
    ]
    if any(work_date not in current_dates for work_date in contiguous_current_dates):
        return "期間先頭の連続勤務判定に必要な日別設定が不足しています"

    for staff in staffs:
        if staff.active_to and staff.active_to < start_date:
            continue
        staff_window_sizes = []
        if weekly_minimum:
            staff_window_sizes.append(7)
        if max_consecutive and staff_in_setting_scope(staff.id, rule_config, "max_consecutive_work_days"):
            staff_window_sizes.append(max_consecutive + 1)
        if max_consecutive_off:
            staff_window_sizes.append(max_consecutive_off + 1)
        if not staff_window_sizes:
            continue
        staff_history_days = max(staff_window_sizes) - 1
        required_prior_dates = [
            work_date
            for work_date in expected_prior_dates[-staff_history_days:]
            if not (
                (staff.active_from and work_date < staff.active_from)
                or (staff.active_to and work_date > staff.active_to)
            )
        ]
        missing = [
            work_date for work_date in required_prior_dates
            if (staff.id, work_date) not in prior_by_key
        ]
        if missing:
            return (
                f"{staff.name} の前期間実績が不足しています: "
                + ", ".join(work_date.isoformat() for work_date in missing)
            )

        combined_dates = expected_prior_dates + contiguous_current_dates
        for window_size in sorted(set(staff_window_sizes)):
            for index in range(max(0, history_days - window_size + 1), history_days):
                window = combined_dates[index : index + window_size]
                if len(window) != window_size or not (window[0] < start_date <= window[-1]):
                    continue
                if any(
                    (staff.active_from and work_date < staff.active_from)
                    or (staff.active_to and work_date > staff.active_to)
                    for work_date in window
                ):
                    continue
                prior_off_count = sum(
                    prior_by_key.get((staff.id, work_date)) == "off"
                    for work_date in window
                    if work_date < start_date
                )
                current_off = [
                    day_off_vars[(staff.id, work_date)]
                    for work_date in window
                    if work_date >= start_date and (staff.id, work_date) in day_off_vars
                ]
                if window_size == 7 and weekly_minimum:
                    model.Add(sum(current_off) + prior_off_count >= weekly_minimum)
                if (window_size == max_consecutive + 1 and max_consecutive
                        and staff_in_setting_scope(staff.id, rule_config, "max_consecutive_work_days")):
                    model.Add(sum(current_off) + prior_off_count >= 1)
                if window_size == max_consecutive_off + 1 and max_consecutive_off:
                    model.Add(sum(current_off) + prior_off_count <= max_consecutive_off)
    return None


def _add_day_off_rules(
    model: cp_model.CpModel,
    staffs: list[Staff],
    demands_by_date: dict,
    variables: dict[tuple[int, int], cp_model.IntVar],
    hard_unavailable: dict[str, set[tuple]],
    hard_working: dict[str, set[tuple]],
    half_day_overrides: dict[tuple[int, object], int | None],
    pair_rules: list[StaffPairRule],
    rule_config: dict,
    penalties: list,
    ordinary_templates: dict[tuple[int, date], ShiftTemplate | None],
    calendar_dates: list[date] | None = None,
    workday_rule_by_date: dict[date, WorkdayRule] | None = None,
    closed_dates: set[date] | None = None,
    default_work_dates: set[date] | None = None,
    half_day_shift_template_ids: set[int] | None = None,
    prior_statuses: list[tuple[int, date, str]] | None = None,
    period_start_date: date | None = None,
    period_end_date_exclusive: date | None = None,
    hard_rule_families: dict[str, set[int]] | None = None,
) -> tuple[
    dict[tuple[int, object], cp_model.IntVar],
    dict[tuple[int, object], cp_model.IntVar],
]:
    dates = sorted(calendar_dates if calendar_dates is not None else demands_by_date)
    closed_dates = closed_dates or set()
    default_work_dates = default_work_dates or set()
    workday_rule_by_date = workday_rule_by_date or {}
    half_day_shift_template_ids = half_day_shift_template_ids or set()
    day_off_vars: dict[tuple[int, object], cp_model.IntVar] = {}
    working_vars: dict[tuple[int, object], cp_model.IntVar] = {}
    weighted_holiday_settings = _resolve_weighted_holiday_settings(rule_config)

    def record_family(name: str, first_constraint: int) -> None:
        if hard_rule_families is None:
            return
        indexes = set(range(first_constraint, len(model.Proto().constraints)))
        if indexes:
            hard_rule_families.setdefault(name, set()).update(indexes)

    def is_active_on_date(staff: Staff, work_date: date) -> bool:
        return not (
            (staff.active_from and work_date < staff.active_from)
            or (staff.active_to and work_date > staff.active_to)
        )

    def active_dates_for(staff: Staff) -> list[date]:
        return [work_date for work_date in dates if is_active_on_date(staff, work_date)]

    staff_weekend_rules = rule_config.get("staff_weekend_rules") or []
    staff_weekend_rule_by_id = {
        int(row["staff_id"]): row for row in staff_weekend_rules
    }
    off_policy_scopes = (
        ("weekly_min_off_days", rule_config.get("weekly_min_off_days") is not None),
        ("monthly_required_off_days", rule_config.get("monthly_required_off_days") is not None),
        ("monthly_min_off_days", rule_config.get("monthly_min_off_days") is not None),
        ("monthly_max_off_days", rule_config.get("monthly_max_off_days") is not None),
        ("max_consecutive_work_days", rule_config.get("max_consecutive_work_days") is not None),
        ("max_consecutive_off_days", rule_config.get("max_consecutive_off_days") is not None),
        ("weekday_off_count", rule_config.get("weekday_off_hard_min") is not None),
        ("day_category_staffing_ranges", bool(rule_config.get("day_category_staffing_ranges"))),
        ("group_day_category_staffing_ranges", bool(rule_config.get("group_day_category_staffing_ranges"))),
        ("staff_day_category_staffing_ranges", bool(rule_config.get("staff_day_category_staffing_ranges"))),
        ("require_monthly_consecutive_off", bool(rule_config.get("require_monthly_consecutive_off"))),
        ("monthly_holiday", bool(weighted_holiday_settings)),
    )

    def staff_weekend_rule_has_off_policy(staff_id: int) -> bool:
        row = staff_weekend_rule_by_id.get(staff_id)
        if not row:
            return False
        return (
            row.get("fixed_weekday_off") is not None
            or int(row.get("minimum_weekend_off_days", 0) or 0) > 0
            or int(row.get("consecutive_weekend_off_count", 0) or 0) > 0
        )

    def staff_has_off_policy(staff_id: int) -> bool:
        return staff_weekend_rule_has_off_policy(staff_id) or any(
            enabled and staff_in_setting_scope(staff_id, rule_config, scope_key)
            for scope_key, enabled in off_policy_scopes
        )

    family_start = len(model.Proto().constraints)
    for staff in staffs:
        for work_date in dates:
            day_demands = demands_by_date[work_date]
            staff_day_vars = [
                variables[(staff.id, demand.id)]
                for demand in day_demands
                if (staff.id, demand.id) in variables
            ]
            working = model.NewBoolVar(f"working_s{staff.id}_{work_date}")
            off = model.NewBoolVar(f"off_s{staff.id}_{work_date}")
            is_active = not (
                (staff.active_from and work_date < staff.active_from)
                or (staff.active_to and work_date > staff.active_to)
            )
            if not is_active or (staff.id, work_date) in hard_unavailable["days"] or work_date in closed_dates:
                family_start = len(model.Proto().constraints)
                model.Add(working == 0)
                record_family("無効期間・休業日・hard休み指定", family_start)
            else:
                if (staff.id, work_date) in hard_working["days"]:
                    family_start = len(model.Proto().constraints)
                    model.Add(working == 1)
                    record_family("hard勤務指定", family_start)
                if staff_day_vars:
                    # 担当名が空欄でも通常の8時間勤務になるため、担当の有無だけで
                    # 休みを決めない。担当がある場合だけ勤務を必須にする。
                    for variable in staff_day_vars:
                        model.Add(variable <= working)
                elif (not day_demands and work_date not in default_work_dates
                      and (staff.id, work_date) not in half_day_overrides):
                    # 勤務枠も日別の基本勤務もない日は、希望だけで勤務扱いにしない。
                    family_start = len(model.Proto().constraints)
                    model.Add(working == 0)
                    record_family("勤務枠・既定勤務なしによる休み強制", family_start)
                if (staff.id, work_date) in half_day_overrides:
                    family_start = len(model.Proto().constraints)
                    model.Add(working == 1)
                    record_family("半日指定による勤務強制", family_start)
                ordinary_template = ordinary_templates[(staff.id, work_date)]
                ordinary_prohibited = ordinary_template is not None and _is_hard_unavailable(
                    staff.id, work_date, ordinary_template.id, hard_unavailable,
                )
                if (
                    work_date in default_work_dates
                    and not staff_has_off_policy(staff.id)
                    and not ordinary_prohibited
                ):
                    model.Add(working == 1)
            model.Add(working + off == 1)
            day_off_vars[(staff.id, work_date)] = off
            working_vars[(staff.id, work_date)] = working

            if (
                is_active
                and work_date not in closed_dates
                and (staff.id, work_date) not in hard_unavailable["days"]
            ):
                avoid_unplanned_off_weight = int(
                    rule_config.get("avoid_unplanned_off_weight", 1) or 0
                )
                if avoid_unplanned_off_weight:
                    penalties.append(off * avoid_unplanned_off_weight)

    weekday_off_hard_min = rule_config.get("weekday_off_hard_min")
    weekday_off_hard_max = rule_config.get("weekday_off_hard_max")
    weekday_off_exempt_staff_ids = {
        int(staff_id) for staff_id in (rule_config.get("weekday_off_exempt_staff_ids") or [])
    }
    weekend_min_staff_count = rule_config.get("weekend_min_staff_count")
    weekend_max_staff_count = rule_config.get("weekend_max_staff_count")
    weekend_preferred_staff_ids = {
        int(staff_id) for staff_id in (rule_config.get("weekend_preferred_staff_ids") or [])
    }
    weekend_comparison_staff_ids = {
        int(staff_id) for staff_id in (rule_config.get("weekend_comparison_staff_ids") or [])
    }
    weekend_preferred_min_advantage = int(
        rule_config.get("weekend_preferred_min_advantage", 0) or 0
    )
    weekend_coverage_staff_ids = {
        int(staff_id) for staff_id in (rule_config.get("weekend_coverage_staff_ids") or [])
    }
    weekend_coverage_min_working = rule_config.get("weekend_coverage_min_working")
    weekend_coverage_max_working = rule_config.get("weekend_coverage_max_working")
    weekend_coverage_target_working = rule_config.get("weekend_coverage_target_working")
    prior_off_by_key = {
        (int(staff_id), _as_date(work_date)): status == "off"
        for staff_id, work_date, status in (prior_statuses or [])
        if status in {"working", "off"}
    }
    for work_date in dates:
        is_special_staffing_date = _is_special_staffing_date(work_date, rule_config)
        active_staff = [staff for staff in staffs if is_active_on_date(staff, work_date)]
        family_start = len(model.Proto().constraints)
        for staff in active_staff:
            staff_weekend_rule = staff_weekend_rule_by_id.get(staff.id)
            allowed_weekday_offs = {
                int(value)
                for value in (staff_weekend_rule.get("allowed_weekday_offs") or [])
                if 0 <= int(value) <= 4
            } if staff_weekend_rule else set()
            if (
                staff_weekend_rule
                and staff_weekend_rule.get("fixed_weekday_off") is not None
                and work_date.weekday() == int(staff_weekend_rule["fixed_weekday_off"])
            ):
                if staff_weekend_rule.get("fixed_weekday_off_priority", "hard") == "hard":
                    model.Add(day_off_vars[(staff.id, work_date)] == 1)
                else:
                    penalties.append(
                        working_vars[(staff.id, work_date)]
                        * int(staff_weekend_rule.get("preference_weight", 100) or 100)
                    )
            if (
                not is_special_staffing_date
                and staff.id in weekend_coverage_staff_ids
                and rule_config.get("weekend_adjustment_policy") == "allow_minimum_weekday_offs"
                and (
                    not staff_weekend_rule
                    or staff_weekend_rule.get("fixed_weekday_off") is None
                    or work_date.weekday() != int(staff_weekend_rule["fixed_weekday_off"])
                )
            ):
                penalties.append(
                    day_off_vars[(staff.id, work_date)]
                    * int(rule_config.get("additional_weekday_off_weight", 100))
                )
            if (
                not is_special_staffing_date
                and staff_weekend_rule
                and not is_allowed_weekday_off(staff_weekend_rule, work_date)
            ):
                model.Add(day_off_vars[(staff.id, work_date)] == 0)
        record_family("固定曜日休み・休める曜日の制限", family_start)
        family_start = len(model.Proto().constraints)
        if not is_special_staffing_date and (weekday_off_hard_min is not None or weekday_off_hard_max is not None):
            counted_off = [
                day_off_vars[(staff.id, work_date)]
                for staff in active_staff
                if staff_in_setting_scope(staff.id, rule_config, "weekday_off_count")
                if staff.id not in weekday_off_exempt_staff_ids
            ]
            if weekday_off_hard_min is not None:
                model.Add(sum(counted_off) >= int(weekday_off_hard_min))
            if weekday_off_hard_max is not None:
                model.Add(sum(counted_off) <= int(weekday_off_hard_max))
        record_family("平日の休み人数", family_start)
        family_start = len(model.Proto().constraints)
        category_staffing = day_category_rule(
            rule_config.get("day_category_staffing_ranges"), work_date, rule_config
        )
        if category_staffing is not None:
            working_for_date = [
                working_vars[(staff.id, work_date)] for staff in active_staff
                if staff_in_setting_scope(staff.id, rule_config, "day_category_staffing_ranges")
            ]
            if category_staffing.get("min_working") is not None:
                model.Add(sum(working_for_date) >= int(category_staffing["min_working"]))
            if category_staffing.get("max_working") is not None:
                model.Add(sum(working_for_date) <= int(category_staffing["max_working"]))
        record_family("標準profileの日区分別出勤人数", family_start)
        family_start = len(model.Proto().constraints)
        for group_rule in rule_config.get("group_day_category_staffing_ranges") or []:
            if group_rule.get("day_category") != day_category(work_date, rule_config):
                continue
            group_working = [
                working_vars[(staff.id, work_date)]
                for staff in active_staff if staff.staff_group == group_rule.get("staff_group")
            ]
            if group_rule.get("min_working") is not None:
                model.Add(sum(group_working) >= int(group_rule["min_working"]))
            if group_rule.get("max_working") is not None:
                model.Add(sum(group_working) <= int(group_rule["max_working"]))
        record_family("設定グループの日区分別出勤人数", family_start)
        family_start = len(model.Proto().constraints)
        for staff_rule in rule_config.get("staff_day_category_staffing_ranges") or []:
            if staff_rule["day_category"] != day_category(work_date, rule_config):
                continue
            scoped_working = [
                working_vars[(staff.id, work_date)]
                for staff in active_staff if staff.id in staff_rule["staff_ids"]
            ]
            if staff_rule.get("min_working") is not None:
                model.Add(sum(scoped_working) >= int(staff_rule["min_working"]))
            if staff_rule.get("max_working") is not None:
                model.Add(sum(scoped_working) <= int(staff_rule["max_working"]))
        record_family("設定職員の日区分別出勤人数", family_start)
        if is_special_staffing_date:
            working_for_date = [working_vars[(staff.id, work_date)] for staff in active_staff]
            family_start = len(model.Proto().constraints)
            if weekend_min_staff_count is not None:
                model.Add(sum(working_for_date) >= int(weekend_min_staff_count))
            if weekend_max_staff_count is not None:
                model.Add(sum(working_for_date) <= int(weekend_max_staff_count))
            record_family("土日祝の全体出勤人数", family_start)
            if weekend_coverage_min_working is not None:
                active_ids = {staff.id for staff in active_staff}
                coverage_working = [
                    working_vars[(staff_id, work_date)]
                    for staff_id in weekend_coverage_staff_ids & active_ids
                ]
                if rule_config.get("weekend_coverage_priority", "hard") == "hard":
                    family_start = len(model.Proto().constraints)
                    model.Add(sum(coverage_working) >= int(weekend_coverage_min_working))
                    record_family("AD・週末coverage出勤人数", family_start)
                else:
                    shortage = model.NewIntVar(
                        0,
                        int(weekend_coverage_min_working),
                        f"weekend_coverage_shortage_{work_date}",
                    )
                    model.Add(
                        shortage
                        >= int(weekend_coverage_min_working) - sum(coverage_working)
                    )
                    penalties.append(
                        shortage * int(rule_config.get("weekend_coverage_weight", 100) or 100)
                    )
                if weekend_coverage_max_working is not None:
                    family_start = len(model.Proto().constraints)
                    model.Add(sum(coverage_working) <= int(weekend_coverage_max_working))
                    record_family("AD・週末coverage出勤人数", family_start)
                if weekend_coverage_target_working is not None:
                    gap = model.NewIntVar(
                        0,
                        max(len(coverage_working), int(weekend_coverage_target_working)),
                        f"weekend_coverage_target_gap_{work_date}",
                    )
                    model.AddAbsEquality(gap, sum(coverage_working) - int(weekend_coverage_target_working))
                    penalties.append(
                        gap * int(rule_config.get("weekend_coverage_target_weight", 100) or 100)
                    )
            if weekend_preferred_min_advantage:
                active_ids = {staff.id for staff in active_staff}
                preferred_working = [
                    working_vars[(staff_id, work_date)]
                    for staff_id in weekend_preferred_staff_ids & active_ids
                ]
                comparison_working = [
                    working_vars[(staff_id, work_date)]
                    for staff_id in weekend_comparison_staff_ids & active_ids
                ]
                family_start = len(model.Proto().constraints)
                model.Add(
                    sum(preferred_working)
                    >= sum(comparison_working) + weekend_preferred_min_advantage
                )
                record_family("legacy指定グループ人数差", family_start)

    family_start = len(model.Proto().constraints)
    max_consecutive_off_days = int(rule_config.get("max_consecutive_off_days", 0) or 0)
    if max_consecutive_off_days:
        window_size = max_consecutive_off_days + 1
        for staff in staffs:
            active_dates = active_dates_for(staff)
            for index in range(len(active_dates) - max_consecutive_off_days):
                window = active_dates[index:index + window_size]
                if (window[-1] - window[0]).days == max_consecutive_off_days:
                    model.Add(sum(day_off_vars[(staff.id, work_date)] for work_date in window) <= max_consecutive_off_days)
    record_family("連続休暇日数の上限", family_start)

    date_set = set(dates)
    effective_start_date = period_start_date or (dates[0] if dates else None)
    effective_end_date = period_end_date_exclusive or (
        dates[-1] + timedelta(days=1) if dates else None
    )
    weekend_pairs: list[tuple[date, date]] = []
    if effective_start_date is not None and effective_end_date is not None:
        for saturday, sunday in saturday_sunday_pairs(
            effective_start_date,
            effective_end_date,
            prior_dates={work_date for _, work_date in prior_off_by_key},
        ):
            if saturday < effective_start_date:
                if sunday in date_set:
                    weekend_pairs.append((saturday, sunday))
                continue
            if saturday in date_set and sunday in date_set:
                weekend_pairs.append((saturday, sunday))
    family_start = len(model.Proto().constraints)
    for staff in staffs:
        row = staff_weekend_rule_by_id.get(staff.id)
        if not row:
            continue
        required_count = int(row.get("consecutive_weekend_off_count", 0) or 0)
        minimum_off_days = int(row.get("minimum_weekend_off_days", 0) or 0)
        if not required_count and not minimum_off_days:
            continue
        pair_vars = []
        for saturday, sunday in weekend_pairs:
            if not is_active_on_date(staff, saturday) or not is_active_on_date(staff, sunday):
                continue
            sunday_off = day_off_vars[(staff.id, sunday)]
            if saturday < effective_start_date:
                prior_key = (staff.id, saturday)
                if prior_key not in prior_off_by_key:
                    continue
                prior_off = int(prior_off_by_key[prior_key])
                if minimum_off_days:
                    model.Add(sunday_off + prior_off >= minimum_off_days)
                if required_count and prior_off:
                    pair_vars.append(sunday_off)
                continue
            saturday_off = day_off_vars[(staff.id, saturday)]
            if minimum_off_days:
                model.Add(saturday_off + sunday_off >= minimum_off_days)
            if required_count:
                pair = model.NewBoolVar(f"weekend_pair_off_s{staff.id}_{saturday}")
                model.Add(pair <= saturday_off)
                model.Add(pair <= sunday_off)
                model.Add(pair >= saturday_off + sunday_off - 1)
                pair_vars.append(pair)
        if required_count:
            if row.get("consecutive_weekend_off_priority", "hard") == "hard":
                model.Add(sum(pair_vars) == required_count)
            else:
                pair_gap = model.NewIntVar(
                    0,
                    max(len(pair_vars), required_count),
                    f"weekend_pair_gap_s{staff.id}",
                )
                model.AddAbsEquality(pair_gap, sum(pair_vars) - required_count)
                penalties.append(pair_gap * int(row.get("preference_weight", 100) or 100))
    record_family("週末ペア休み条件", family_start)

    from .operational_constraints import daily_staffing_rules
    operational_family_names = {
        "日別の出勤人数": "日別勤務設定の全体出勤人数",
        "土日祝の出勤人数": "土日祝の全体出勤人数",
        "土日祝の指定メンバー出勤人数": "AD・週末coverage出勤人数",
        "土日祝の指定グループ間の人数差": "legacy指定グループ人数差",
    }
    for _, label, expression, relation, bound in daily_staffing_rules(
        staffs, dates, working_vars, workday_rule_by_date, rule_config,
    ):
        family_start = len(model.Proto().constraints)
        model.Add(expression >= bound if relation == "ge" else expression <= bound)
        family_name = operational_family_names.get(label)
        if family_name is None and label.startswith("日別の") and label.endswith("の出勤人数"):
            family_name = "日別勤務設定のグループ出勤人数"
        if family_name is not None:
            record_family(family_name, family_start)

    weekend_fairness_weight = int(rule_config.get("weekend_fairness_weight", 0) or 0)
    weekend_dates = [work_date for work_date in dates if _is_special_staffing_date(work_date, rule_config)]
    if weekend_fairness_weight and weekend_dates:
        weekend_workloads = []
        for staff in staffs:
            workload = model.NewIntVar(0, len(weekend_dates), f"weekend_work_s{staff.id}")
            model.Add(
                workload
                == sum(working_vars[(staff.id, work_date)] for work_date in weekend_dates)
            )
            weekend_workloads.append(workload)
        max_weekend_work = model.NewIntVar(0, len(weekend_dates), "max_weekend_work")
        min_weekend_work = model.NewIntVar(0, len(weekend_dates), "min_weekend_work")
        model.AddMaxEquality(max_weekend_work, weekend_workloads)
        model.AddMinEquality(min_weekend_work, weekend_workloads)
        weekend_gap = model.NewIntVar(0, len(weekend_dates), "weekend_work_gap")
        model.Add(weekend_gap == max_weekend_work - min_weekend_work)
        penalties.append(weekend_gap * weekend_fairness_weight)

    family_start = len(model.Proto().constraints)
    weekly_min_off_days = rule_config.get("weekly_min_off_days")
    if weekly_min_off_days:
        for staff in staffs:
            for start_index in range(0, len(dates) - 6):
                week_dates = dates[start_index : start_index + 7]
                if (week_dates[-1] - week_dates[0]).days != 6:
                    continue
                if not all(is_active_on_date(staff, work_date) for work_date in week_dates):
                    continue
                model.Add(
                    sum(day_off_vars[(staff.id, work_date)] for work_date in week_dates)
                    >= int(weekly_min_off_days)
                )
    record_family("7日間の最低休暇日数", family_start)

    family_start = len(model.Proto().constraints)
    max_consecutive_work_days = rule_config.get("max_consecutive_work_days")
    if max_consecutive_work_days:
        max_consecutive_work_days = int(max_consecutive_work_days)
        window_size = max_consecutive_work_days + 1
        if window_size <= len(dates):
            for staff in staffs:
                if not staff_in_setting_scope(staff.id, rule_config, "max_consecutive_work_days"):
                    continue
                for start_index in range(0, len(dates) - max_consecutive_work_days):
                    window = dates[start_index : start_index + window_size]
                    if (window[-1] - window[0]).days != max_consecutive_work_days:
                        continue
                    if not all(is_active_on_date(staff, work_date) for work_date in window):
                        continue
                    model.Add(
                        sum(
                            1 - day_off_vars[(staff.id, work_date)]
                            for work_date in window
                        )
                        <= max_consecutive_work_days
                    )
    record_family("連続勤務日数の上限", family_start)

    family_start = len(model.Proto().constraints)
    monthly_required_off_days = rule_config.get("monthly_required_off_days")
    if monthly_required_off_days is not None and len(dates) >= 28:
        for staff in staffs:
            active_dates = active_dates_for(staff)
            if len(active_dates) < 28:
                continue
            model.Add(
                sum(day_off_vars[(staff.id, work_date)] for work_date in active_dates)
                == int(monthly_required_off_days)
            )
    elif len(dates) >= 28:
        monthly_min_off_days = rule_config.get("monthly_min_off_days")
        monthly_max_off_days = rule_config.get("monthly_max_off_days")
        if monthly_min_off_days is not None:
            for staff in staffs:
                active_dates = active_dates_for(staff)
                if not active_dates:
                    continue
                model.Add(
                    sum(day_off_vars[(staff.id, work_date)] for work_date in active_dates)
                    >= int(monthly_min_off_days)
                )
        if monthly_max_off_days is not None:
            for staff in staffs:
                active_dates = active_dates_for(staff)
                if not active_dates:
                    continue
                model.Add(
                    sum(day_off_vars[(staff.id, work_date)] for work_date in active_dates)
                    <= int(monthly_max_off_days)
                )
    record_family("期間内の単純休暇日数", family_start)

    family_start = len(model.Proto().constraints)
    if rule_config.get("require_monthly_consecutive_off") and len(dates) >= 14:
        consecutive_off_strength = str(
            rule_config.get("monthly_consecutive_off_strength", "hard")
        ).lower()
        consecutive_off_weight = int(
            rule_config.get("monthly_consecutive_off_weight", 1) or 1
        )
        consecutive_off_exempt_ids = {
            int(value) for value in rule_config.get("consecutive_off_exempt_staff_ids") or []
        }
        for staff in staffs:
            if staff.id in consecutive_off_exempt_ids:
                continue
            if not staff_in_setting_scope(staff.id, rule_config, "require_monthly_consecutive_off"):
                continue
            active_dates = active_dates_for(staff)
            consecutive_pair_vars = []
            if not active_dates:
                continue
            for first, second in zip(active_dates, active_dates[1:], strict=False):
                if (second - first).days != 1:
                    continue
                pair = model.NewBoolVar(f"consecutive_off_s{staff.id}_{first}")
                model.AddBoolAnd(
                    [day_off_vars[(staff.id, first)], day_off_vars[(staff.id, second)]]
                ).OnlyEnforceIf(pair)
                model.AddBoolOr(
                    [
                        day_off_vars[(staff.id, first)].Not(),
                        day_off_vars[(staff.id, second)].Not(),
                    ]
                ).OnlyEnforceIf(pair.Not())
                consecutive_pair_vars.append(pair)
            if consecutive_pair_vars:
                if consecutive_off_strength == "hard":
                    model.AddBoolOr(consecutive_pair_vars)
                else:
                    has_consecutive_off = model.NewBoolVar(
                        f"has_consecutive_off_s{staff.id}"
                    )
                    model.AddMaxEquality(has_consecutive_off, consecutive_pair_vars)
                    penalties.append((1 - has_consecutive_off) * consecutive_off_weight)
            elif consecutive_off_strength == "hard":
                model.AddBoolOr([])
    record_family("期間内の2日連続休暇", family_start)

    family_start = len(model.Proto().constraints)
    staff_ids = {staff.id for staff in staffs}
    from .operational_constraints import hard_pair_constraint
    for rule in pair_rules:
        if rule.first_staff_id not in staff_ids or rule.second_staff_id not in staff_ids:
            continue
        if rule.kind in {"avoid_same_weekday_off", "avoid_same_day_off"}:
            for work_date in dates:
                if rule.kind == "avoid_same_weekday_off" and work_date.weekday() >= 5:
                    continue
                first_off = day_off_vars[(rule.first_staff_id, work_date)]
                second_off = day_off_vars[(rule.second_staff_id, work_date)]
                if rule.strength == "hard":
                    model.Add(hard_pair_constraint(rule.kind, first_off, second_off))
                    continue
                overlap = model.NewBoolVar(f"pair_off_overlap_{rule.id}_{work_date}")
                model.AddBoolAnd([first_off, second_off]).OnlyEnforceIf(overlap)
                model.AddBoolOr([first_off.Not(), second_off.Not()]).OnlyEnforceIf(overlap.Not())
                penalties.append(overlap * max(1, rule.weight))
        elif rule.kind == "avoid_same_day_assignment":
            for work_date in dates:
                first_vars = [
                    variables[(rule.first_staff_id, demand.id)]
                    for demand in demands_by_date[work_date]
                    if (rule.first_staff_id, demand.id) in variables
                ]
                second_vars = [
                    variables[(rule.second_staff_id, demand.id)]
                    for demand in demands_by_date[work_date]
                    if (rule.second_staff_id, demand.id) in variables
                ]
                if not first_vars or not second_vars:
                    continue
                first_assigned = model.NewBoolVar(f"pair_first_assigned_{rule.id}_{work_date}")
                second_assigned = model.NewBoolVar(f"pair_second_assigned_{rule.id}_{work_date}")
                model.Add(sum(first_vars) >= first_assigned)
                model.Add(sum(first_vars) <= len(first_vars) * first_assigned)
                model.Add(sum(second_vars) >= second_assigned)
                model.Add(sum(second_vars) <= len(second_vars) * second_assigned)
                if rule.strength == "hard":
                    model.Add(hard_pair_constraint(rule.kind, first_assigned, second_assigned))
                    continue
                overlap = model.NewBoolVar(f"pair_assignment_overlap_{rule.id}_{work_date}")
                model.AddBoolAnd([first_assigned, second_assigned]).OnlyEnforceIf(overlap)
                model.AddBoolOr([first_assigned.Not(), second_assigned.Not()]).OnlyEnforceIf(overlap.Not())
                penalties.append(overlap * max(1, rule.weight))
        elif rule.kind in {"require_same_day_work", "require_same_day_assignment"}:
            for work_date in dates:
                if rule.kind == "require_same_day_work":
                    first_present = working_vars[(rule.first_staff_id, work_date)]
                    second_present = working_vars[(rule.second_staff_id, work_date)]
                else:
                    first_vars = [
                        variables[(rule.first_staff_id, demand.id)]
                        for demand in demands_by_date[work_date]
                        if (rule.first_staff_id, demand.id) in variables
                    ]
                    second_vars = [
                        variables[(rule.second_staff_id, demand.id)]
                        for demand in demands_by_date[work_date]
                        if (rule.second_staff_id, demand.id) in variables
                    ]
                    first_present = _assignment_presence(model, first_vars, f"pair_first_{rule.id}_{work_date}")
                    second_present = _assignment_presence(model, second_vars, f"pair_second_{rule.id}_{work_date}")
                if rule.strength == "hard":
                    model.Add(hard_pair_constraint(rule.kind, first_present, second_present))
                else:
                    difference = model.NewIntVar(0, 1, f"pair_requirement_gap_{rule.id}_{work_date}")
                    model.AddAbsEquality(difference, first_present - second_present)
                    penalties.append(difference * max(1, rule.weight))
    record_family("職員組み合わせ条件", family_start)

    spread_weight = int(rule_config.get("weekday_off_spread_weight", 0) or 0)
    if spread_weight:
        target_min = int(rule_config.get("weekday_off_target_min", 0) or 0)
        target_max = int(rule_config.get("weekday_off_target_max", 3) or 3)
        for work_date in dates:
            if _is_special_staffing_date(work_date, rule_config):
                continue
            active_staff = [
                staff
                for staff in staffs
                if is_active_on_date(staff, work_date)
                and staff_in_setting_scope(staff.id, rule_config, "weekday_off_count")
                and staff.id not in weekday_off_exempt_staff_ids
            ]
            weekday_off_count = model.NewIntVar(
                0, len(active_staff), f"weekday_off_count_{work_date}"
            )
            model.Add(
                weekday_off_count
                == sum(day_off_vars[(staff.id, work_date)] for staff in active_staff)
            )
            over_three = model.NewIntVar(
                0, len(active_staff), f"weekday_off_over_three_{work_date}"
            )
            model.AddMaxEquality(over_three, [weekday_off_count - target_max, 0])
            penalties.append(over_three * spread_weight)
            if target_min:
                under_target = model.NewIntVar(
                    0, len(active_staff), f"weekday_off_under_target_{work_date}"
                )
                model.AddMaxEquality(under_target, [target_min - weekday_off_count, 0])
                penalties.append(under_target * spread_weight)

    return day_off_vars, working_vars


def _load_availability(
    session: Session, start_date, end_date
) -> tuple[
    dict[str, set[tuple]],
    dict,
    dict[tuple[int, object], int | None],
    dict,
    dict[str, set[tuple]],
]:
    rows = (
        session.query(Availability)
        .filter(Availability.work_date >= start_date)
        .filter(Availability.work_date < end_date)
        .all()
    )
    hard_unavailable = {"days": set(), "shifts": set()}
    soft_request_off = {}
    half_day_overrides: dict[tuple[int, object], int | None] = {}
    soft_request_work = {}
    hard_working = {"days": set(), "shifts": set()}
    for row in rows:
        day_key = (row.staff_id, row.work_date)
        shift_key = (row.staff_id, row.work_date, row.shift_template_id)
        if row.status == "unavailable" or (row.status == "request_off" and row.strength == "hard"):
            if row.shift_template_id is None:
                hard_unavailable["days"].add(day_key)
            else:
                hard_unavailable["shifts"].add(shift_key)
        elif row.status == "request_off":
            soft_request_off[shift_key] = row.weight
        elif row.status == "half_day":
            half_day_overrides[day_key] = row.shift_template_id
        elif row.status in {"request_work", "preferred_work"}:
            if row.status == "request_work" and row.strength == "hard":
                if row.shift_template_id is None:
                    hard_working["days"].add(day_key)
                else:
                    hard_working["shifts"].add(shift_key)
            else:
                soft_request_work[shift_key] = row.weight
    return hard_unavailable, soft_request_off, half_day_overrides, soft_request_work, hard_working


def _is_hard_unavailable(
    staff_id: int,
    work_date: date,
    shift_template_id: int,
    hard_unavailable: dict[str, set[tuple]],
) -> bool:
    return (staff_id, work_date) in hard_unavailable["days"] or (
        staff_id,
        work_date,
        shift_template_id,
    ) in hard_unavailable["shifts"]


def _soft_request_weight(
    requests: dict[tuple[int, date, int | None], int],
    staff_id: int,
    work_date: date,
    shift_template_id: int,
) -> int | None:
    values = [
        requests[key]
        for key in (
            (staff_id, work_date, None),
            (staff_id, work_date, shift_template_id),
        )
        if key in requests
    ]
    return max(values) if values else None


def _mark_infeasible(session: Session, solve_run: SolveRun, message: str, *, commit: bool = True) -> SolveRun:
    solve_run.status = "failed"
    solve_run.solver_status = "INFEASIBLE"
    solve_run.finished_at = datetime.now(UTC)
    from .jobs import record_execution_terminal
    record_execution_terminal(solve_run)
    session.add(
        Violation(
            solve_run_id=solve_run.id,
            severity="hard",
            rule_name="INFEASIBLE",
            message=message,
            penalty=100000,
        )
    )
    if commit:
        session.commit()
    return solve_run




def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)
