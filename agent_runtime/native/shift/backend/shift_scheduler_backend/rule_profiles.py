from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .models import Demand, Position, RuleConfig, RuleProfile, ShiftTemplate, Staff, WorkdayRule
from .periods import build_active_settings
from .rule_config_utils import day_category


INITIAL_PROFILE_NAME = "standard-operational-rules-v1"
INITIAL_GROUP_A_NAMES = ["ス", "ティ", "ゾ", "トー", "キン", "ピョー", "チー", "セイン", "チャン"]
INITIAL_GROUP_B_NAMES = ["リン", "ハー", "タム", "ニー", "ガン", "チン"]
INITIAL_POSITION_ALIASES = {
    "ふた": {"ふた"},
    "うしろ": {"うしろ"},
    "が": {"が"},
    "まえ": {"まえ", "がまえ"},
    "5あ": {"5あ", "5あ洗"},
    "6あ": {"6あ", "6あ洗"},
}


class RuleProfileError(ValueError):
    pass


def _unique_by_name(rows, names: list[str], *, label: str) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for name in names:
        matches = [row for row in rows if row.name == name or getattr(row, "display_name", None) == name]
        if len(matches) != 1:
            raise RuleProfileError(f"標準ルールの{label}「{name}」を一意に解決できません")
        resolved[name] = matches[0]
    return resolved


def _unique_positions_by_alias(rows) -> dict[str, Position]:
    resolved: dict[str, Position] = {}
    for meaning, aliases in INITIAL_POSITION_ALIASES.items():
        matches = [
            row for row in rows
            if row.name in aliases or getattr(row, "display_name", None) in aliases
        ]
        if len(matches) != 1:
            raise RuleProfileError(f"標準ルールの持ち場「{meaning}」を一意に解決できません")
        resolved[meaning] = matches[0]
    return resolved


def initialize_initial_profile(session: Session) -> RuleProfile | None:
    existing = session.query(RuleProfile).filter(RuleProfile.name == INITIAL_PROFILE_NAME).one_or_none()
    if existing is not None:
        return existing
    staffs = session.query(Staff).all()
    trigger_names = set(INITIAL_GROUP_A_NAMES + INITIAL_GROUP_B_NAMES)
    if not trigger_names.issubset({staff.name for staff in staffs}):
        return None
    staff_by_name = _unique_by_name(staffs, list(trigger_names), label="人員")
    position_by_name = _unique_positions_by_alias(session.query(Position).all())
    config = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    half_day_ids = {
        int(staff_id): int(template_id)
        for staff_id, template_id in ((config.config_json if config else {}).get("staff_half_day_shift_template_ids") or {}).items()
    }
    member_ids = [int(staff_by_name[name].id) for name in INITIAL_GROUP_A_NAMES + INITIAL_GROUP_B_NAMES]
    missing_half_day = [staff_id for staff_id in member_ids if staff_id not in half_day_ids]
    if missing_half_day:
        raise RuleProfileError(f"標準ルール対象者の半日勤務テンプレートが未設定です: {missing_half_day}")
    profile = RuleProfile(
        name=INITIAL_PROFILE_NAME,
        enabled=True,
        profile_json={
            "version": 1,
            "member_staff_ids": member_ids,
            "weekday_no_off_staff_ids": [staff_by_name["ス"].id, staff_by_name["ピョー"].id],
            "consecutive_off_exempt_staff_ids": [staff_by_name["ス"].id, staff_by_name["ピョー"].id],
            "off_overlap_pairs": [
                [staff_by_name["ゾ"].id, staff_by_name["ティ"].id],
                [staff_by_name["ティ"].id, staff_by_name["チャン"].id],
                [staff_by_name["チー"].id, staff_by_name["セイン"].id],
            ],
            "weekday_position_assignments": [
                {"staff_id": staff_by_name["ハー"].id, "position_id": position_by_name["ふた"].id},
                {"staff_id": staff_by_name["ニー"].id, "position_id": position_by_name["うしろ"].id},
            ],
            "auto_assignment_prohibited_position_ids": [
                position_by_name[name].id for name in ["が", "まえ", "5あ", "6あ"]
            ],
            "weekend_balance_groups": [
                [staff_by_name[name].id for name in INITIAL_GROUP_A_NAMES],
                [staff_by_name[name].id for name in INITIAL_GROUP_B_NAMES],
            ],
            "staff_half_day_shift_template_ids": {
                str(staff_id): half_day_ids[staff_id] for staff_id in member_ids
            },
            "settings": {
                "max_consecutive_work_days": 6,
                "require_prior_period_continuity": True,
                "monthly_holiday_min_days": 8,
                "monthly_holiday_max_days": 9,
                "holiday_count_unit": 2,
                "holiday_counted_work_kinds": {"off": 1.0, "half_day": 0.5, "regular": 0.0, "working": 0.0, "position": 0.0},
                "auto_half_day_allowed_weekdays": [6],
                "require_monthly_consecutive_off": True,
                "monthly_consecutive_off_strength": "hard",
                "weekday_off_hard_min": 1,
                "weekday_off_hard_max": 2,
                "weekday_off_spread_weight": 100,
                "weekday_off_target_min": 1,
                "weekday_off_target_max": 1,
                "day_category_staffing_ranges": [
                    {"day_category": "saturday", "min_working": 7, "max_working": 7},
                    {"day_category": "sunday", "min_working": 6, "max_working": 7},
                ],
                "sunday_target_work_count": 2,
                "sunday_target_work_weight": 100,
                "weekend_group_balance_weight": 100,
                "weekend_preferred_min_advantage": 0,
            },
            "profile_scoped_setting_keys": [
                "monthly_holiday", "max_consecutive_work_days",
                "require_monthly_consecutive_off", "weekday_off_count",
                "day_category_staffing_ranges", "sunday_half_day",
                "sunday_target_work_count", "weekend_balance_groups",
            ],
        },
    )
    session.add(profile)
    session.flush()
    return profile


def effective_rule_config(session: Session) -> dict:
    profiles = session.query(RuleProfile).filter(RuleProfile.enabled.is_(True)).order_by(RuleProfile.id).all()
    if len(profiles) > 1:
        raise RuleProfileError("有効な標準ルールprofileが複数あります")
    config = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    base = build_active_settings(config.config_json if config else {})
    if not profiles:
        return base
    payload = profiles[0].profile_json or {}
    _validate_profile_ids(session, payload)
    base["weekend_preferred_min_advantage"] = 0
    merged = {**base, **(payload.get("settings") or {})}
    merged["staff_half_day_shift_template_ids"] = {
        **(base.get("staff_half_day_shift_template_ids") or {}),
        **(payload.get("staff_half_day_shift_template_ids") or {}),
    }
    for key in (
        "member_staff_ids", "weekday_no_off_staff_ids", "consecutive_off_exempt_staff_ids",
        "off_overlap_pairs", "weekday_position_assignments",
        "auto_assignment_prohibited_position_ids", "weekend_balance_groups",
        "profile_scoped_setting_keys",
    ):
        merged[key] = payload.get(key, [])
    merged["active_rule_profile"] = {"id": profiles[0].id, "name": profiles[0].name, "version": payload.get("version")}
    return build_active_settings(merged)


def materialize_required_profile_demands(
    session: Session,
    start_date: date,
    end_date_exclusive: date,
    settings: dict,
) -> list[Demand]:
    assignments = settings.get("weekday_position_assignments") or []
    if not settings.get("active_rule_profile") or not assignments:
        return []

    from .editor_revision_service import (
        claim_collection, claim_item, lock_solver_demand_revision, technical_revision,
    )
    collection_revision = lock_solver_demand_revision(session)

    position_ids = {int(row["position_id"]) for row in assignments}
    existing_position_ids = {
        row[0] for row in session.query(Position.id).filter(Position.id.in_(position_ids)).all()
    }
    missing_positions = sorted(position_ids - existing_position_ids)
    if missing_positions:
        raise RuleProfileError(f"標準ルールprofileの固定担当持ち場IDが見つかりません: {missing_positions}")

    weekdays = [
        start_date + timedelta(days=offset)
        for offset in range((end_date_exclusive - start_date).days)
        if day_category(start_date + timedelta(days=offset), settings) == "weekday"
    ]
    workdays = {
        row.work_date: row
        for row in session.query(WorkdayRule).filter(
            WorkdayRule.work_date >= start_date,
            WorkdayRule.work_date < end_date_exclusive,
        ).all()
    }
    required_slots: Counter[tuple[date, int, int]] = Counter()
    for work_date in weekdays:
        workday = workdays.get(work_date)
        if workday is None:
            raise RuleProfileError(f"標準ルールprofileの固定担当に必要な {work_date} の日別勤務設定がありません")
        if workday.kind != "normal" or workday.shift_template_id is None:
            raise RuleProfileError(f"標準ルールprofileの固定担当に必要な {work_date} の通常勤務テンプレートを解決できません")
        if session.get(ShiftTemplate, workday.shift_template_id) is None:
            raise RuleProfileError(f"標準ルールprofileの固定担当に必要な {work_date} の勤務テンプレートIDが見つかりません")
        for assignment in assignments:
            required_slots[(work_date, int(workday.shift_template_id), int(assignment["position_id"]))] += 1

    existing = {
        (row.work_date, row.shift_template_id, row.position_id): row
        for row in session.query(Demand).filter(
            Demand.work_date >= start_date,
            Demand.work_date < end_date_exclusive,
        ).all()
    }
    materialized = []
    changes = []
    for key, required_count in sorted(required_slots.items()):
        demand = existing.get(key)
        if demand is None:
            demand = Demand(
                work_date=key[0],
                shift_template_id=key[1],
                position_id=key[2],
                required_count=required_count,
            )
            materialized.append(demand)
            continue
        revision = technical_revision(demand.revision, "demand_revision")
        values = (
            max(demand.required_count, required_count),
            max(demand.max_count, required_count) if demand.max_count is not None else None,
            max(demand.ideal_count, required_count) if demand.ideal_count is not None else None,
        )
        if values != (demand.required_count, demand.max_count, demand.ideal_count):
            changes.append((demand, revision, values))
    if materialized or changes:
        claim_collection(session, "demands", collection_revision)
        for demand, revision, values in sorted(changes, key=lambda item: item[0].id):
            claim_item(session, Demand, demand.id, revision, "demand_revision")
            demand.required_count, demand.max_count, demand.ideal_count = values
        session.add_all(materialized)
    session.flush()
    return materialized


def _validate_profile_ids(session: Session, payload: dict) -> None:
    if payload.get("version") != 1:
        raise RuleProfileError("有効な標準ルールprofileのversionを解釈できません")
    existing_staff_ids = {row[0] for row in session.query(Staff.id).all()}
    existing_position_ids = {row[0] for row in session.query(Position.id).all()}
    staff_ids = {
        int(value)
        for key in ("member_staff_ids", "weekday_no_off_staff_ids", "consecutive_off_exempt_staff_ids")
        for value in payload.get(key) or []
    }
    for pair in payload.get("off_overlap_pairs") or []:
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            raise RuleProfileError("標準ルールprofileの休み重複禁止pairが不正です")
        staff_ids.update(int(value) for value in pair)
    groups = payload.get("weekend_balance_groups") or []
    if len(groups) != 2 or any(not group for group in groups):
        raise RuleProfileError("標準ルールprofileの週末balance groupが不正です")
    for group in groups:
        if len(group) != len(set(group)):
            raise RuleProfileError("標準ルールprofileのgroup membershipが重複しています")
        staff_ids.update(int(value) for value in group)
    if set(groups[0]).intersection(groups[1]):
        raise RuleProfileError("標準ルールprofileのgroup membershipが重複しています")
    assignments = payload.get("weekday_position_assignments") or []
    staff_ids.update(int(row["staff_id"]) for row in assignments)
    position_ids = {
        int(row["position_id"]) for row in assignments
    } | {int(value) for value in payload.get("auto_assignment_prohibited_position_ids") or []}
    half_day_ids = {int(value) for value in (payload.get("staff_half_day_shift_template_ids") or {}).keys()}
    staff_ids.update(half_day_ids)
    missing_staff = sorted(staff_ids - existing_staff_ids)
    missing_positions = sorted(position_ids - existing_position_ids)
    if missing_staff:
        raise RuleProfileError(f"標準ルールprofileの人員IDが見つかりません: {missing_staff}")
    if missing_positions:
        raise RuleProfileError(f"標準ルールprofileの持ち場IDが見つかりません: {missing_positions}")
