from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import set_committed_value
from src.models.route_diagram import RouteDiagram
from src.services.route_diagram_service import blockers_for_diagrams
from src.services import school_lunch_ingredient_settings as ingredient_settings

from src.models.school_lunch import (
    DailyWorkSchedule,
    Facility,
    Ingredient,
    MenuAssignee,
    MenuExecution,
    MenuIngredient,
    ProcessStep,
)


class SchoolLunchEntityNotFound(LookupError):
    pass


class SchoolLunchConflict(ValueError):
    pass


def _ingredient_attributes_match(ingredient: Ingredient, payload: dict[str, Any]) -> bool:
    return all(payload.get(field) is None or getattr(ingredient, field) is None
               or getattr(ingredient, field) == payload[field] for field in ingredient_settings.FIELDS)


def _require_matching_ingredient_attributes(ingredient: Ingredient, payload: dict[str, Any]) -> None:
    if not _ingredient_attributes_match(ingredient, payload):
        raise SchoolLunchConflict(f"ingredient master attributes do not match: {ingredient.name}")
    # Fill only explicitly supplied, previously unconfigured master fields.
    # Other menu lines keep their own unconfirmed mask until reviewed there.
    for field in ingredient_settings.FIELDS:
        if getattr(ingredient, field) is None and payload.get(field) is not None:
            setattr(ingredient, field, payload[field])


def _validate_process_step_sequences(payload: dict[str, Any]) -> None:
    for row in payload["ingredients"]:
        sequences = [step["sequence"] for step in row["process_steps"]]
        if len(sequences) != len(set(sequences)):
            raise SchoolLunchConflict("process step sequences must be unique within an ingredient")


def _validate_assignees(payload: dict[str, Any]) -> None:
    assignees = payload.get("assignees", [])
    if len(assignees) != len(set(assignees)):
        raise SchoolLunchConflict("assignees must be unique within a menu execution")


def _load_menu(session: Session, menu_execution_id: int) -> MenuExecution:
    statement = (
        select(MenuExecution)
        .where(MenuExecution.id == menu_execution_id)
        .options(
            selectinload(MenuExecution.facility),
            selectinload(MenuExecution.assignees),
            selectinload(MenuExecution.menu_ingredients).selectinload(MenuIngredient.ingredient),
            selectinload(MenuExecution.menu_ingredients).selectinload(MenuIngredient.process_steps),
        )
    )
    menu = session.execute(statement).scalar_one_or_none()
    if menu is None:
        raise SchoolLunchEntityNotFound(f"menu execution {menu_execution_id} was not found")
    return menu


def _serialize_menu(menu: MenuExecution) -> dict[str, Any]:
    return {
        "id": menu.id,
        "facility": {"id": menu.facility.id, "name": menu.facility.name},
        "service_date": menu.service_date,
        "name": menu.name,
        "servings": menu.servings,
        "cooking_instructions": menu.cooking_instructions,
        "cooking_instruction_sources": menu.cooking_instruction_sources,
        "status": menu.status,
        "version": menu.version,
        "assignees": [assignee.name for assignee in menu.assignees],
        "ingredients": [
            {
                "id": row.id,
                "ingredient": vars(ingredient_settings.for_row(row)),
                "required_quantity": row.required_quantity,
                "unit": row.unit,
                "process_text": row.process_text,
                "route_color": row.route_color,
                "process_steps": [
                    {
                        "id": step.id,
                        "sequence": step.sequence,
                        "location": step.location,
                        "operation": step.operation,
                        "scheduled_time": step.scheduled_time,
                        "work_zone": step.work_zone,
                        "route_line_style": step.route_line_style,
                    }
                    for step in row.process_steps
                ],
            }
            for row in menu.menu_ingredients
        ],
    }


def create_menu_execution(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    _validate_process_step_sequences(payload)
    _validate_assignees(payload)
    for row in payload["ingredients"]:
        if row.get("id") is not None or any(step.get("id") is not None for step in row["process_steps"]):
            raise SchoolLunchConflict("new menu rows and process steps must not reuse existing ids")
    facility_name = payload["facility_name"]
    facility = session.execute(select(Facility).where(Facility.name == facility_name)).scalar_one_or_none()
    if facility is None:
        raise SchoolLunchConflict("学校を先に登録してください。取込から学校は自動作成できません。")

    existing = session.execute(
        select(MenuExecution).where(
            MenuExecution.facility == facility,
            MenuExecution.service_date == payload["service_date"],
            MenuExecution.name == payload["name"],
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise SchoolLunchConflict("the menu execution already exists")

    menu = MenuExecution(
        facility=facility,
        service_date=payload["service_date"],
        name=payload["name"],
        servings=payload["servings"],
        cooking_instructions=payload.get("cooking_instructions", ""),
        cooking_instruction_sources=payload.get("cooking_instruction_sources", []),
        source_document_id=payload.get("source_document_id"),
        status=payload["status"],
        version=payload["version"],
    )
    session.add(menu)
    session.flush()

    for position, name in enumerate(payload.get("assignees", []), start=1):
        session.add(MenuAssignee(menu_execution=menu, position=position, name=name))

    for row in payload["ingredients"]:
        ingredient_data = row["ingredient"]
        ingredient_name = ingredient_data["name"]
        ingredient = session.execute(select(Ingredient).where(Ingredient.name == ingredient_name).with_for_update()).scalar_one_or_none()
        if ingredient is None:
            ingredient = Ingredient(
                name=ingredient_name,
                **{field: ingredient_data.get(field) for field in ingredient_settings.FIELDS},
            )
            session.add(ingredient)
            session.flush()
        else:
            _require_matching_ingredient_attributes(ingredient, ingredient_data)
        line = MenuIngredient(
            menu_execution=menu,
            ingredient=ingredient,
            required_quantity=row["required_quantity"],
            unit=row["unit"],
            process_text=row["process_text"],
            route_color=row["route_color"],
            unconfirmed_ingredient_fields=ingredient_settings.unconfirmed_fields(ingredient_data),
        )
        session.add(line)
        session.flush()
        for step_data in row["process_steps"]:
            session.add(ProcessStep(menu_ingredient=line, **step_data))

    session.flush()
    return _serialize_menu(_load_menu(session, menu.id))


def create_menu_execution_batch(session: Session, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not payloads:
        raise SchoolLunchConflict("at least one confirmed menu is required")
    return [create_menu_execution(session, payload) for payload in payloads]


def get_menu_execution(session: Session, menu_execution_id: int) -> dict[str, Any]:
    return _serialize_menu(_load_menu(session, menu_execution_id))


def list_menu_executions(session: Session, facility_id=None, start=None, end=None) -> list[dict[str, Any]]:
    statement = (
        select(MenuExecution)
        .options(
            selectinload(MenuExecution.facility),
            selectinload(MenuExecution.assignees),
            selectinload(MenuExecution.menu_ingredients).selectinload(MenuIngredient.ingredient),
            selectinload(MenuExecution.menu_ingredients).selectinload(MenuIngredient.process_steps),
        )
        .order_by(MenuExecution.service_date.desc(), MenuExecution.id.desc())
    )
    if facility_id is not None:
        statement = statement.where(MenuExecution.facility_id == facility_id)
    if start is not None:
        statement = statement.where(MenuExecution.service_date >= start, MenuExecution.service_date < end)
    return [_serialize_menu(menu) for menu in session.execute(statement).scalars().all()]


def get_monthly_overview(session: Session, month: str) -> dict[str, Any]:
    try:
        year_text, month_text = month.split("-", maxsplit=1)
        year = int(year_text)
        month_number = int(month_text)
        start = date(year, month_number, 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM format") from exc
    end = date(year, month_number, monthrange(year, month_number)[1])

    facilities = session.execute(select(Facility).order_by(Facility.name)).scalars().all()
    menus = session.execute(
        select(MenuExecution).where(MenuExecution.service_date.between(start, end))
    ).scalars().all()
    schedules = session.execute(
        select(DailyWorkSchedule).where(DailyWorkSchedule.service_date.between(start, end))
    ).scalars().all()

    menu_index: dict[tuple[int, date], list[MenuExecution]] = {}
    for menu in menus:
        menu_index.setdefault((menu.facility_id, menu.service_date), []).append(menu)
    schedule_index = {(schedule.facility_id, schedule.service_date): schedule for schedule in schedules}
    schedule_stale = {
        key: schedule.source_menu_versions != {str(menu.id): menu.version for menu in menu_index.get(key, [])}
        for key, schedule in schedule_index.items()
    }
    diagrams = session.execute(
        select(RouteDiagram).where(RouteDiagram.service_date.between(start, end))
    ).scalars().all()
    diagram_blockers = blockers_for_diagrams(session, diagrams)
    diagram_index = {
        (diagram.facility_id, diagram.service_date): {
            "route_diagram_id": diagram.id,
            "route_diagram_version": diagram.version,
            "route_layout_id": diagram.layout_id,
            "route_layout_version": diagram.layout_version,
            "route_diagram_blockers": diagram_blockers[diagram.id],
        }
        for diagram in diagrams
    }

    return {
        "month": f"{year:04d}-{month_number:02d}",
        "start_date": start,
        "end_date": end,
        "facilities": [
            {
                "id": facility.id,
                "name": facility.name,
                "days": [
                    {
                        "date": service_date,
                        "work_schedule_stale": schedule_stale.get((facility.id, service_date), False),
                        **diagram_index.get((facility.id, service_date), {
                            "route_diagram_id": None,
                            "route_diagram_version": None,
                            "route_layout_id": None,
                            "route_layout_version": None,
                            "route_diagram_blockers": [],
                        }),
                        "menu_count": len(menu_index.get((facility.id, service_date), [])),
                        "menu_statuses": sorted({
                            menu.status for menu in menu_index.get((facility.id, service_date), [])
                        }),
                        "work_schedule_id": (
                            schedule_index[(facility.id, service_date)].id
                            if (facility.id, service_date) in schedule_index
                            else None
                        ),
                        "work_schedule_status": (
                            schedule_index[(facility.id, service_date)].status
                            if (facility.id, service_date) in schedule_index
                            else None
                        ),
                    }
                    for day in range(1, end.day + 1)
                    for service_date in [date(year, month_number, day)]
                ],
            }
            for facility in facilities
        ],
    }


def update_menu_execution(session: Session, menu_execution_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    menu = _load_menu(session, menu_execution_id)
    _validate_process_step_sequences(payload)
    _validate_assignees(payload)
    if payload["version"] != menu.version:
        raise SchoolLunchConflict("the menu execution was changed by another operation")
    result = session.execute(update(MenuExecution).where(
        MenuExecution.id == menu_execution_id, MenuExecution.version == payload["version"]
    ).values(version=payload["version"] + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise SchoolLunchConflict("the menu execution was changed by another operation")
    # Reserve the parent revision before touching any child or shared master.
    set_committed_value(menu, "version", payload["version"] + 1)

    duplicate_menu = session.execute(
        select(MenuExecution).where(
            MenuExecution.id != menu_execution_id,
            MenuExecution.facility_id == menu.facility_id,
            MenuExecution.service_date == payload["service_date"],
            MenuExecution.name == payload["name"],
        )
    ).scalar_one_or_none()
    if duplicate_menu is not None:
        raise SchoolLunchConflict("the menu execution already exists")

    existing_lines = {line.id: line for line in menu.menu_ingredients}
    new_ingredients = {}
    submitted_line_ids = [row["id"] for row in payload["ingredients"] if row.get("id") is not None]
    if len(submitted_line_ids) != len(set(submitted_line_ids)):
        raise SchoolLunchConflict("a menu ingredient cannot be submitted more than once")
    plans = []
    retained_line_ids: set[int] = set()
    for row in payload["ingredients"]:
        line_id = row.get("id")
        line = existing_lines.get(line_id) if line_id is not None else None
        if line_id is not None and line is None:
            raise SchoolLunchEntityNotFound(f"menu ingredient {line_id} was not found")
        ingredient_data = row["ingredient"]
        ingredient_id = ingredient_data.get("id")
        ingredient_name = ingredient_data["name"]
        ingredient_with_name = session.execute(
            select(Ingredient).where(Ingredient.name == ingredient_name).with_for_update()
        ).scalar_one_or_none()
        if ingredient_id is not None:
            current_ingredient = session.get(Ingredient, ingredient_id)
            if current_ingredient is None:
                raise SchoolLunchEntityNotFound(f"ingredient {ingredient_id} was not found")
            # The editor keeps the original material id while changing its name.
            # This is an explicit replacement only for that original line.
            if current_ingredient.name != ingredient_name and (line is None or line.ingredient_id != ingredient_id):
                raise SchoolLunchConflict("ingredient id and name do not identify the same material")
        ingredient = ingredient_with_name or new_ingredients.get(ingredient_name)
        if ingredient is None:
            ingredient = Ingredient(
                name=ingredient_name,
                **{field: ingredient_data.get(field) for field in ingredient_settings.FIELDS},
            )
            new_ingredients[ingredient_name] = ingredient
        else:
            _require_matching_ingredient_attributes(ingredient, ingredient_data)
        if line is not None:
            if line.id in retained_line_ids:
                raise SchoolLunchConflict("multiple input rows resolve to the same menu ingredient")
            retained_line_ids.add(line.id)
        existing_step_ids = {step.id for step in line.process_steps} if line is not None else set()
        submitted_step_ids = [step["id"] for step in row["process_steps"] if step.get("id") is not None]
        if len(submitted_step_ids) != len(set(submitted_step_ids)):
            raise SchoolLunchConflict("a process step cannot be submitted more than once")
        for step_id in submitted_step_ids:
            if step_id not in existing_step_ids:
                raise SchoolLunchEntityNotFound(f"process step {step_id} was not found")
        plans.append((row, ingredient, line))

    menu.name = payload["name"]
    menu.service_date = payload["service_date"]
    menu.servings = payload["servings"]
    menu.status = payload["status"]
    for field in ("cooking_instructions", "cooking_instruction_sources"):
        if payload.get(field) is not None:
            setattr(menu, field, payload[field])

    for line in list(menu.menu_ingredients):
        if line.id in retained_line_ids:
            line.process_steps.clear()
    menu.assignees.clear()
    session.flush()

    for position, name in enumerate(payload.get("assignees", []), start=1):
        menu.assignees.append(MenuAssignee(position=position, name=name))

    step_plans = []
    for row, ingredient, old_line in plans:
        if old_line is None:
            line = MenuIngredient()
            menu.menu_ingredients.append(line)
        else:
            line = old_line
        line.ingredient = ingredient
        line.required_quantity = row["required_quantity"]
        line.unit = row["unit"]
        line.process_text = row["process_text"]
        line.route_color = row["route_color"]
        line.unconfirmed_ingredient_fields = ingredient_settings.unconfirmed_fields(row["ingredient"])
        session.flush()
        step_plans.extend((step, line) for step in row["process_steps"])
    # Allocate new row identities before deleting old ones, including SQLite
    # databases that may otherwise reuse the removed maximum primary key.
    for line_id, line in existing_lines.items():
        if line_id not in retained_line_ids:
            menu.menu_ingredients.remove(line)
    session.flush()
    # Flush all retained step ids before new auto-generated ids for the same
    # reason; submitted step ownership was checked against the original rows.
    for step_data, line in sorted(step_plans, key=lambda plan: plan[0].get("id") is None):
        line.process_steps.append(ProcessStep(**step_data))
        session.flush()

    session.flush()
    session.expire_all()
    return _serialize_menu(_load_menu(session, menu_execution_id))
