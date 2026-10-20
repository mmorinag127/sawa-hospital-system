"""School-scoped, immutable process-pattern revisions and their daily use."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint

from src.db import Base


def now():
    return datetime.now(timezone.utc)


class ProcessPattern(Base):
    __tablename__ = "school_lunch_process_patterns"

    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False, default=0)
    head_revision_id = Column(Integer, nullable=True)


class ProcessPatternRevision(Base):
    __tablename__ = "school_lunch_process_pattern_revisions"
    __table_args__ = (UniqueConstraint("pattern_id", "number", name="uq_process_pattern_revision_number"),)

    id = Column(Integer, primary_key=True)
    pattern_id = Column(Integer, ForeignKey("school_lunch_process_patterns.id"), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    parent_revision_id = Column(Integer, ForeignKey("school_lunch_process_pattern_revisions.id"), nullable=True)
    material_slots = Column(JSON, nullable=False)
    document = Column(JSON, nullable=False)
    fixed_equipment = Column(JSON, nullable=False)
    execution_choices = Column(JSON, nullable=False)
    change_note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ProcessPatternApplication(Base):
    """Immutable record of the pattern revisions assembled into one daily revision."""
    __tablename__ = "school_lunch_process_pattern_applications"
    __table_args__ = (UniqueConstraint("process_revision_id", "application_id",
                        name="uq_process_pattern_application_revision"),
                       Index("ix_process_pattern_application_revision", "process_revision_id"))

    id = Column(Integer, primary_key=True)
    process_revision_id = Column(Integer, ForeignKey("school_lunch_process_revisions.id"), nullable=False)
    pattern_revision_id = Column(Integer, ForeignKey("school_lunch_process_pattern_revisions.id"), nullable=False)
    application_id = Column(String(80), nullable=False)
    material_mapping = Column(JSON, nullable=False)
    execution_selections = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
