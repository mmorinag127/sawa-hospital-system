from __future__ import annotations

from datetime import date
from sqlalchemy import Boolean, CheckConstraint, Column, Date, ForeignKey, Integer, JSON, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import relationship

from src.db import Base


class StaffMember(Base):
    __tablename__ = "school_lunch_staff"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_staff_name"),
        CheckConstraint("version >= 1", name="ck_school_lunch_staff_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)


class Facility(Base):
    __tablename__ = "school_lunch_facilities"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_facility_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)

    menu_executions = relationship("MenuExecution", back_populates="facility")
    work_schedules = relationship("DailyWorkSchedule", back_populates="facility")


class Ingredient(Base):
    __tablename__ = "school_lunch_ingredients"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_ingredient_name"),
        CheckConstraint("length(trim(stock_unit)) > 0", name="ck_school_lunch_ingredient_stock_unit"),
        CheckConstraint("length(trim(order_unit)) > 0", name="ck_school_lunch_ingredient_order_unit"),
        CheckConstraint("conversion_rate > 0", name="ck_school_lunch_ingredient_conversion_rate"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    category = Column(String(100), nullable=True)
    stock_unit = Column(String(32), nullable=True)
    order_unit = Column(String(32), nullable=True)
    conversion_rate = Column(Numeric(18, 6), nullable=True)

    menu_ingredients = relationship("MenuIngredient", back_populates="ingredient")


class MenuExecution(Base):
    __tablename__ = "school_lunch_menu_executions"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_menu_name"),
        CheckConstraint("servings > 0", name="ck_school_lunch_menu_servings"),
        CheckConstraint("version >= 1", name="ck_school_lunch_menu_version"),
        UniqueConstraint("facility_id", "service_date", "name", name="uq_school_lunch_menu_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_date = Column(Date, nullable=False, index=True, default=date.today)
    name = Column(String(200), nullable=False)
    servings = Column(Integer, nullable=False)
    cooking_instructions = Column(Text, nullable=False, default="", server_default="")
    cooking_instruction_sources = Column(JSON, nullable=False, default=list, server_default="[]")
    source_document_id = Column(String(32), ForeignKey("school_lunch_documents.id", ondelete="RESTRICT"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)

    facility = relationship("Facility", back_populates="menu_executions")
    menu_ingredients = relationship(
        "MenuIngredient",
        back_populates="menu_execution",
        cascade="all, delete-orphan",
        order_by="MenuIngredient.id",
    )
    assignees = relationship(
        "MenuAssignee",
        back_populates="menu_execution",
        cascade="all, delete-orphan",
        order_by="MenuAssignee.position",
    )
    work_schedule_rows = relationship("WorkScheduleRow", back_populates="menu_execution")


class MenuAssignee(Base):
    __tablename__ = "school_lunch_menu_assignees"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_school_lunch_menu_assignee_position"),
        CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_menu_assignee_name"),
        UniqueConstraint("menu_execution_id", "position", name="uq_school_lunch_menu_assignee_position"),
        UniqueConstraint("menu_execution_id", "name", name="uq_school_lunch_menu_assignee_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_execution_id = Column(
        Integer,
        ForeignKey("school_lunch_menu_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)

    menu_execution = relationship("MenuExecution", back_populates="assignees")


class MenuIngredient(Base):
    __tablename__ = "school_lunch_menu_ingredients"
    __table_args__ = (
        CheckConstraint("required_quantity >= 0", name="ck_school_lunch_menu_ingredient_quantity"),
        CheckConstraint("length(trim(unit)) > 0", name="ck_school_lunch_menu_ingredient_unit"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_execution_id = Column(
        Integer,
        ForeignKey("school_lunch_menu_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ingredient_id = Column(
        Integer, ForeignKey("school_lunch_ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    required_quantity = Column(Numeric(18, 6), nullable=False)
    unit = Column(String(32), nullable=False)
    process_text = Column(String(1000), nullable=False, default="")
    route_color = Column(String(7), nullable=False, default="#2563eb")

    menu_execution = relationship("MenuExecution", back_populates="menu_ingredients")
    ingredient = relationship("Ingredient", back_populates="menu_ingredients")
    # A source import must not adopt unrelated shared-master settings just
    # because its material name matches. Existing rows retain their settings.
    unconfirmed_ingredient_fields = Column(JSON, nullable=False, default=list, server_default="[]")
    process_steps = relationship(
        "ProcessStep",
        back_populates="menu_ingredient",
        cascade="all, delete-orphan",
        order_by="ProcessStep.sequence",
    )


class ProcessStep(Base):
    __tablename__ = "school_lunch_process_steps"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_school_lunch_process_step_sequence"),
        CheckConstraint("length(trim(location)) > 0", name="ck_school_lunch_process_step_location"),
        CheckConstraint("length(trim(operation)) > 0", name="ck_school_lunch_process_step_operation"),
        CheckConstraint(
            "work_zone IS NULL OR work_zone IN ('contaminated', 'contaminated_meat_fish', 'non_contaminated')",
            name="ck_school_lunch_process_step_work_zone",
        ),
        CheckConstraint(
            "route_line_style IS NULL OR route_line_style IN ('solid', 'dashed')",
            name="ck_school_lunch_process_step_line_style",
        ),
        UniqueConstraint("menu_ingredient_id", "sequence", name="uq_school_lunch_process_step_sequence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_ingredient_id = Column(
        Integer, ForeignKey("school_lunch_menu_ingredients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)
    location = Column(String(200), nullable=False)
    operation = Column(String(300), nullable=False)
    scheduled_time = Column(Time, nullable=True)
    work_zone = Column(String(32), nullable=True)
    route_line_style = Column(String(16), nullable=True)

    menu_ingredient = relationship("MenuIngredient", back_populates="process_steps")


class DailyWorkSchedule(Base):
    __tablename__ = "school_lunch_daily_work_schedules"
    __table_args__ = (
        CheckConstraint("plating_end > plating_start", name="ck_school_lunch_work_schedule_plating_range"),
        CheckConstraint("length(trim(status)) > 0", name="ck_school_lunch_work_schedule_status"),
        CheckConstraint("version >= 1", name="ck_school_lunch_work_schedule_version"),
        UniqueConstraint("facility_id", "service_date", name="uq_school_lunch_work_schedule_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_date = Column(Date, nullable=False, index=True)
    plating_start = Column(Time, nullable=False)
    plating_end = Column(Time, nullable=False)
    timing_profile_id = Column(Integer, nullable=True)
    timing_profile_version = Column(Integer, nullable=True)
    timeline_ticks = Column(JSON, nullable=True)
    delivery_assignees = Column(JSON, nullable=True)
    source_notes = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    time_profile = Column(String(16), nullable=True)
    source_menu_versions = Column(JSON, nullable=True)
    source_process_revisions = Column(JSON, nullable=True)

    facility = relationship("Facility", back_populates="work_schedules")
    rows = relationship(
        "WorkScheduleRow",
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="WorkScheduleRow.position",
    )


class WorkScheduleRow(Base):
    __tablename__ = "school_lunch_work_schedule_rows"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_school_lunch_work_schedule_row_position"),
        CheckConstraint("length(trim(menu_name)) > 0", name="ck_school_lunch_work_schedule_row_menu_name"),
        UniqueConstraint("schedule_id", "position", name="uq_school_lunch_work_schedule_row_position"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(
        Integer,
        ForeignKey("school_lunch_daily_work_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    menu_execution_id = Column(
        Integer,
        ForeignKey("school_lunch_menu_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position = Column(Integer, nullable=False)
    menu_name = Column(String(200), nullable=False)
    menu_version = Column(Integer, nullable=True)

    schedule = relationship("DailyWorkSchedule", back_populates="rows")
    menu_execution = relationship("MenuExecution", back_populates="work_schedule_rows")
    assignees = relationship(
        "WorkScheduleAssignee",
        back_populates="row",
        cascade="all, delete-orphan",
        order_by="WorkScheduleAssignee.position",
    )
    tasks = relationship(
        "WorkScheduleTask",
        back_populates="row",
        cascade="all, delete-orphan",
        order_by="WorkScheduleTask.sequence",
    )


class WorkScheduleAssignee(Base):
    __tablename__ = "school_lunch_work_schedule_assignees"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_school_lunch_work_schedule_assignee_position"),
        CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_work_schedule_assignee_name"),
        UniqueConstraint("row_id", "position", name="uq_school_lunch_work_schedule_assignee_position"),
        UniqueConstraint("row_id", "name", name="uq_school_lunch_work_schedule_assignee_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    row_id = Column(
        Integer,
        ForeignKey("school_lunch_work_schedule_rows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)

    row = relationship("WorkScheduleRow", back_populates="assignees")


class WorkScheduleTask(Base):
    __tablename__ = "school_lunch_work_schedule_tasks"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_school_lunch_work_schedule_task_sequence"),
        CheckConstraint("end_time > start_time", name="ck_school_lunch_work_schedule_task_time_range"),
        CheckConstraint("length(trim(operation)) > 0", name="ck_school_lunch_work_schedule_task_operation"),
        CheckConstraint(
            "work_zone IN ('contaminated', 'contaminated_meat_fish', 'non_contaminated')",
            name="ck_school_lunch_work_schedule_task_work_zone",
        ),
        UniqueConstraint("row_id", "sequence", name="uq_school_lunch_work_schedule_task_sequence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    row_id = Column(
        Integer,
        ForeignKey("school_lunch_work_schedule_rows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    offset_before_plating_minutes = Column(Integer, nullable=True)
    operation = Column(String(500), nullable=False)
    work_zone = Column(String(32), nullable=True)
    assignees = Column(JSON, nullable=True)
    ingredient_name = Column(String(200), nullable=False, default="")
    phase = Column(String(20), nullable=True)
    kettle = Column(String(1), nullable=True)
    kettle_use = Column(Integer, nullable=True)
    uses_raw_meat = Column(Boolean, nullable=True)

    row = relationship("WorkScheduleRow", back_populates="tasks")
