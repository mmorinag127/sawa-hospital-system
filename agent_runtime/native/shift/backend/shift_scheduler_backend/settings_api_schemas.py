"""Documentation of settings/parser payloads; no runtime response filtering."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class HolidayCountedWorkKindsResponse(SettingsResponse):
    off: float
    half_day: float
    regular: float
    working: float
    position: float


class StaffWeekendRuleResponse(SettingsResponse):
    staff_id: int
    minimum_weekend_off_days: int | None = None
    fixed_weekday_off: int | None = None
    allowed_weekday_offs: list[int] | None = None
    fixed_weekday_off_priority: Literal["hard", "prefer"] | None = None
    consecutive_weekend_off_count: int | None = None
    consecutive_weekend_off_priority: Literal["hard", "prefer"] | None = None
    preference_weight: int | None = None


class DayCategoryStaffingRangeResponse(SettingsResponse):
    day_category: Literal["weekday", "saturday", "sunday", "holiday"]
    min_working: int | None = None
    max_working: int | None = None


class GroupDayCategoryStaffingRangeResponse(DayCategoryStaffingRangeResponse):
    staff_group: str


class StaffDayCategoryStaffingRangeResponse(DayCategoryStaffingRangeResponse):
    staff_ids: list[int]


class PositionDayCategoryProhibitionResponse(SettingsResponse):
    position_id: int
    day_categories: list[Literal["weekday", "saturday", "sunday", "holiday"]]


class PositionStaffingGroupResponse(SettingsResponse):
    position_id: int
    staff_ids: list[int]
    min_count: int | None = None


class OptimizationSettingsResponse(SettingsResponse):
    settings_revision: str
    strategy: str
    standard_period_mode: Literal["calendar_month", "fixed_start_day"]
    standard_period_start_day: int | None
    max_period_days: int
    holiday_count_unit: int
    holiday_counted_work_kinds: HolidayCountedWorkKindsResponse
    monthly_holiday_exact_days: float | None
    monthly_holiday_min_days: float | None
    monthly_holiday_max_days: float | None
    auto_apply_japanese_holidays: bool
    forbid_half_day_on_weekdays: bool
    forbid_half_day_on_saturdays: bool
    auto_half_day_allowed_weekdays: list[int] | None
    max_daily_work_minutes: int | None
    max_weekly_work_minutes: int | None
    max_monthly_work_minutes: int | None
    min_rest_minutes: int
    min_monthly_work_minutes: int | None
    half_day_work_minutes: int
    full_day_work_minutes: int
    weekly_min_off_days: int | None
    require_prior_period_continuity: bool
    monthly_required_off_days: int | None
    monthly_min_off_days: int | None
    monthly_max_off_days: int | None
    max_consecutive_work_days: int | None
    max_consecutive_off_days: int | None
    require_monthly_consecutive_off: bool
    monthly_consecutive_off_strength: Literal["hard", "soft"]
    monthly_consecutive_off_weight: int
    coverage_weight: int | None
    balanced_shift_types: list[str]
    weekday_off_spread_weight: int
    weekday_off_target_min: int
    weekday_off_target_max: int
    avoid_unplanned_off_weight: int
    consecutive_work_weight: int
    target_work_minutes_weight: int
    weekend_fairness_weight: int
    shift_fairness_weight: int
    staff_half_day_shift_template_ids: dict[str, int]
    staff_display_order_ids: list[int]
    regular_work_display_blank: bool
    weekday_off_hard_max: int | None
    weekday_off_hard_min: int | None
    weekday_off_exempt_staff_ids: list[int]
    weekend_min_staff_count: int | None
    weekend_max_staff_count: int | None
    weekend_preferred_staff_ids: list[int]
    weekend_comparison_staff_ids: list[int]
    weekend_preferred_min_advantage: int
    staff_weekend_rules: list[StaffWeekendRuleResponse]
    weekend_coverage_staff_ids: list[int]
    weekend_coverage_min_working: int | None
    weekend_coverage_max_working: int | None
    weekend_coverage_target_working: int | None
    weekend_coverage_priority: Literal["hard", "prefer"]
    weekend_coverage_weight: int
    weekend_coverage_target_weight: int
    weekend_adjustment_policy: Literal["ask", "allow_minimum_weekday_offs"]
    additional_weekday_off_weight: int
    day_category_staffing_ranges: list[DayCategoryStaffingRangeResponse]
    group_day_category_staffing_ranges: list[GroupDayCategoryStaffingRangeResponse]
    staff_day_category_staffing_ranges: list[StaffDayCategoryStaffingRangeResponse]
    position_day_category_prohibitions: list[PositionDayCategoryProhibitionResponse]
    position_staffing_groups: list[PositionStaffingGroupResponse]


class RulePreflightPeriodResponse(SettingsResponse):
    start_date: date
    end_date_exclusive: date
    weekend_days: int
    full_weekends: int


class StaffRuleSummaryResponse(SettingsResponse):
    staff_id: int
    staff_name: str
    fixed_weekday_off: int | None
    fixed_weekday_label: str | None
    fixed_weekday_count: int
    allowed_weekday_offs: list[int] | None
    consecutive_weekend_off_count: int


class RuleSuggestionResponse(SettingsResponse):
    action: str
    label: str
    staff_id: int | None = None
    value: int | None = None
    settings_patch: dict[str, Any] | None = None


class RulePreflightIssueResponse(SettingsResponse):
    severity: Literal["error", "action_required", "warning"]
    code: str
    message: str
    minimum_additional_weekday_offs: int | None = None
    required_weekend_offs: int | None = None
    weekend_off_capacity: int | None = None
    suggestions: list[RuleSuggestionResponse]


class RulePreflightResponse(SettingsResponse):
    blocking: bool
    period: RulePreflightPeriodResponse
    staff_rule_summaries: list[StaffRuleSummaryResponse]
    issues: list[RulePreflightIssueResponse]


class RuleAssistantPairRuleResponse(SettingsResponse):
    first_staff_id: int
    second_staff_id: int
    kind: Literal["avoid_same_day_off"]
    strength: Literal["hard", "soft"]
    weight: int
    reason: str | None


class RuleAssistantStaffUpdateResponse(SettingsResponse):
    staff_id: int
    paid_leave_eligible: bool


class RuleAssistantItemVersionResponse(SettingsResponse):
    id: int
    revision: int


class RuleAssistantCollectionVersionsResponse(SettingsResponse):
    collection_revision: int
    items: list[RuleAssistantItemVersionResponse]


class RuleAssistantInputVersionsResponse(SettingsResponse):
    staff: RuleAssistantCollectionVersionsResponse
    pair_rules: RuleAssistantCollectionVersionsResponse
    settings_revision: str


class RuleAssistantCandidateResponse(SettingsResponse):
    provider: Literal["structured_rule_parser"]
    settings_patch: dict[str, Any]
    explanations: list[str]
    questions: list[str]
    requires_confirmation: Literal[True]
    pair_rule_candidates: list[RuleAssistantPairRuleResponse]
    staff_updates: list[RuleAssistantStaffUpdateResponse]
    input_versions: RuleAssistantInputVersionsResponse


class RuleAssistantApplyResponse(SettingsResponse):
    settings: OptimizationSettingsResponse
    staff_updates_applied: int
    pair_rules_applied: int
    input_versions: RuleAssistantInputVersionsResponse
