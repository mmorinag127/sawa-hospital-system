from uuid import uuid4

from sqlalchemy import delete, select
from loguru import logger

from src.db import session_scope
from src.models.menu import MenuRule
from src.services.menu_vocabulary import normalize_diet_type


ALLOWED_RULE_TYPES = {"global", "menu", "facility"}
ALLOWED_MATCH_TYPES = {"exact", "contains", "regex"}

DEFAULT_GLOBAL_RULES = [
    {"daypart": "朝食", "category": "主菜", "unit_type": "g", "qty_per_serving": 70},
    {"daypart": "朝食", "category": "副菜", "unit_type": "g", "qty_per_serving": 40},
    {"daypart": "昼食", "category": "主菜", "unit_type": "g", "qty_per_serving": 100},
    {"daypart": "昼食", "category": "副菜", "unit_type": "g", "qty_per_serving": 40},
    {"daypart": "夕食", "category": "主菜", "unit_type": "g", "qty_per_serving": 100},
    {"daypart": "夕食", "category": "副菜", "unit_type": "g", "qty_per_serving": 40},
]


def _serialize_rule(rule: MenuRule) -> dict:
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "match_type": rule.match_type,
        "menu_pattern": rule.menu_pattern,
        "facility_id": rule.facility_id,
        "daypart": rule.daypart,
        "category": rule.category,
        "diet_type": normalize_diet_type(rule.diet_type),
        "unit_type": rule.unit_type,
        "qty_per_serving": rule.qty_per_serving,
        "priority": rule.priority,
        "active": rule.active,
    }


def list_rules(rule_type: str | None = None) -> list[dict]:
    try:
        with session_scope() as session:
            stmt = select(MenuRule)
            if rule_type:
                stmt = stmt.where(MenuRule.rule_type == rule_type)
            rules = session.execute(stmt).scalars().all()
            return [_serialize_rule(rule) for rule in rules]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Menu rule query failed", error=str(exc))
        return []


def create_rule(payload: dict) -> dict:
    rule_type = str(payload.get("rule_type") or "").strip()
    if rule_type not in ALLOWED_RULE_TYPES:
        raise ValueError("invalid rule_type")
    match_type = payload.get("match_type")
    if match_type and match_type not in ALLOWED_MATCH_TYPES:
        raise ValueError("invalid match_type")
    qty_value = payload.get("qty_per_serving")
    if qty_value is not None and qty_value != "":
        try:
            qty_value = float(qty_value)
        except Exception:
            qty_value = None
    else:
        qty_value = None

    priority_value = payload.get("priority")
    if priority_value is not None and priority_value != "":
        try:
            priority_value = int(priority_value)
        except Exception:
            priority_value = None
    else:
        priority_value = None

    rule = MenuRule(
        id=f"MRU{uuid4().hex[:8]}",
        rule_type=rule_type,
        match_type=match_type,
        menu_pattern=payload.get("menu_pattern"),
        facility_id=payload.get("facility_id"),
        daypart=payload.get("daypart"),
        category=payload.get("category"),
        diet_type=normalize_diet_type(payload.get("diet_type")),
        unit_type=payload.get("unit_type"),
        qty_per_serving=qty_value,
        priority=priority_value,
        active=bool(payload.get("active", True)),
    )
    with session_scope() as session:
        session.add(rule)
        session.flush()
        session.refresh(rule)
        return _serialize_rule(rule)


def update_rule(rule_id: str, payload: dict) -> bool:
    with session_scope() as session:
        rule = session.get(MenuRule, rule_id)
        if not rule:
            return False
        if "qty_per_serving" in payload:
            qty_value = payload.get("qty_per_serving")
            if qty_value is not None and qty_value != "":
                try:
                    payload["qty_per_serving"] = float(qty_value)
                except Exception:
                    payload["qty_per_serving"] = None
            else:
                payload["qty_per_serving"] = None
        if "priority" in payload:
            priority_value = payload.get("priority")
            if priority_value is not None and priority_value != "":
                try:
                    payload["priority"] = int(priority_value)
                except Exception:
                    payload["priority"] = None
            else:
                payload["priority"] = None
        for field in [
            "rule_type",
            "match_type",
            "menu_pattern",
            "facility_id",
            "daypart",
            "category",
            "diet_type",
            "unit_type",
            "qty_per_serving",
            "priority",
            "active",
        ]:
            if field in payload:
                value = payload[field]
                if field == "diet_type":
                    value = normalize_diet_type(value)
                setattr(rule, field, value)
        return True


def delete_rule(rule_id: str) -> bool:
    with session_scope() as session:
        result = session.execute(delete(MenuRule).where(MenuRule.id == rule_id))
        return result.rowcount > 0


def list_active_rules() -> list[dict]:
    try:
        with session_scope() as session:
            rules = (
                session.execute(select(MenuRule).where(MenuRule.active.is_(True)))
                .scalars()
                .all()
            )
            return [_serialize_rule(rule) for rule in rules]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Menu rule query failed", error=str(exc))
        return []


def ensure_default_rules() -> bool:
    try:
        with session_scope() as session:
            existing = session.execute(select(MenuRule.id).limit(1)).first()
            if existing:
                return False
            for rule in DEFAULT_GLOBAL_RULES:
                session.add(
                    MenuRule(
                        id=f"MRU{uuid4().hex[:8]}",
                        rule_type="global",
                        match_type=None,
                        menu_pattern=None,
                        facility_id=None,
                        daypart=rule["daypart"],
                        category=rule["category"],
                        diet_type=None,
                        unit_type=rule["unit_type"],
                        qty_per_serving=rule["qty_per_serving"],
                        priority=0,
                        active=True,
                    )
                )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Menu rule seed failed", error=str(exc))
        return False
