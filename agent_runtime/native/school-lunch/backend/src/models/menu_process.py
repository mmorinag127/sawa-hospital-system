"""Editable recipe graph: append-only revisions; bindings pin daily outputs."""
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from src.db import Base


def now():
    return datetime.now(timezone.utc)


class ProcessRecipe(Base):
    __tablename__ = 'school_lunch_process_recipes'
    __table_args__ = (UniqueConstraint('facility_id', 'transfer_source_key',
                        name='uq_school_lunch_process_recipes_transfer_source_key'),)
    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey('school_lunch_facilities.id'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False, default=0)
    head_revision_id = Column(Integer, nullable=True)
    confirmed_revision_id = Column(Integer, nullable=True)
    transfer_source_key = Column(String(300), nullable=True)
    transfer_source_hash = Column(String(64), nullable=True)


class ProcessRevision(Base):
    __tablename__ = 'school_lunch_process_revisions'
    __table_args__ = (UniqueConstraint('recipe_id', 'number', name='uq_process_revision_number'),)
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey('school_lunch_process_recipes.id'), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    parent_revision_id = Column(Integer, ForeignKey('school_lunch_process_revisions.id'), nullable=True)
    menu_execution_id = Column(Integer, ForeignKey('school_lunch_menu_executions.id'), nullable=True)
    scope = Column(String(10), nullable=False)
    status = Column(String(12), nullable=False)
    origin = Column(String(30), nullable=False)
    actor_role = Column(String(30), nullable=False)
    document = Column(JSON, nullable=False)
    source_snapshot = Column(JSON, nullable=False)
    changes = Column(JSON, nullable=False)
    user_constraints = Column(JSON, nullable=False, default=dict)
    change_note = Column(Text, nullable=False, default='')
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ProcessBinding(Base):
    __tablename__ = 'school_lunch_process_bindings'
    menu_execution_id = Column(Integer, ForeignKey('school_lunch_menu_executions.id'), primary_key=True)
    recipe_id = Column(Integer, ForeignKey('school_lunch_process_recipes.id'), nullable=False)
    revision_id = Column(Integer, ForeignKey('school_lunch_process_revisions.id'), nullable=False)
    version = Column(Integer, nullable=False)


class ProcessProposal(Base):
    __tablename__ = 'school_lunch_process_proposals'
    id = Column(Integer, primary_key=True)
    menu_execution_id = Column(Integer, ForeignKey('school_lunch_menu_executions.id'), nullable=False, index=True)
    base_version = Column(Integer, nullable=False)
    model = Column(String(200), nullable=False)
    prompt_hash = Column(String(64), nullable=False)
    example_revision_ids = Column(JSON, nullable=False)
    input_document = Column(JSON, nullable=False)
    document = Column(JSON, nullable=False)
    inference_trace = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
