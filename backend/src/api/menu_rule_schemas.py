"""Response documentation only: never used for runtime response validation."""

from pydantic import BaseModel, ConfigDict


class MenuRuleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    revision: int
    rule_type: str
    match_type: str | None
    menu_pattern: str | None
    facility_id: str | None
    daypart: str | None
    category: str | None
    diet_type: str | None
    unit_type: str | None
    qty_per_serving: float | None
    priority: int | None
    active: bool


class MenuRuleListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    rules: list[MenuRuleResponse]
    revision: str | None


class MenuRuleSaveResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    rule: MenuRuleResponse
    revision: str


class MenuRuleUpdateResponse(MenuRuleSaveResponse):
    updated: bool


class MenuRuleDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    deleted: bool
    revision: str
