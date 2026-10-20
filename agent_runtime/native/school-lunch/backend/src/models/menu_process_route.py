"""Immutable facility drawing-profile revisions and generated process routes."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from src.db import Base
from src.models.menu_process import now


class ProcessRouteProfile(Base):
    __tablename__ = "school_lunch_process_route_profiles"
    __table_args__ = (UniqueConstraint("facility_id", "transfer_source_key",
                        name="uq_school_lunch_process_route_profiles_transfer_source_key"),)
    id = Column(Integer, primary_key=True)
    facility_id = Column(
        Integer, ForeignKey("school_lunch_facilities.id"), nullable=False, index=True
    )
    name = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False, default=0)
    head_revision_id = Column(Integer)
    confirmed_revision_id = Column(Integer)
    transfer_source_key = Column(String(300))
    transfer_source_hash = Column(String(64))


class ProcessRouteProfileRevision(Base):
    __tablename__ = "school_lunch_process_route_profile_revisions"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "number", name="uq_process_route_profile_revision_number"
        ),
    )
    id = Column(Integer, primary_key=True)
    profile_id = Column(
        Integer,
        ForeignKey("school_lunch_process_route_profiles.id"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    parent_revision_id = Column(
        Integer, ForeignKey("school_lunch_process_route_profile_revisions.id")
    )
    status = Column(String(12), nullable=False)
    template_id = Column(String(80), nullable=False)
    template_version = Column(Integer, nullable=False)
    svg_snapshot = Column(Text, nullable=False)
    manifest_snapshot = Column(JSON, nullable=False)
    network_snapshot = Column(JSON, nullable=False)
    snapshot_hashes = Column(JSON, nullable=False)
    document = Column(JSON, nullable=False)
    metadata_json = Column(JSON, nullable=False)
    change_note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ProcessRouteArtifact(Base):
    __tablename__ = "school_lunch_process_route_artifacts"
    id = Column(Integer, primary_key=True)
    menu_execution_id = Column(
        Integer,
        ForeignKey("school_lunch_menu_executions.id"),
        nullable=False,
        index=True,
    )
    process_revision_id = Column(
        Integer, ForeignKey("school_lunch_process_revisions.id"), nullable=False
    )
    profile_revision_id = Column(
        Integer,
        ForeignKey("school_lunch_process_route_profile_revisions.id"),
        nullable=False,
    )
    renderer_version = Column(String(120), nullable=False)
    input_hash = Column(String(64), nullable=False)
    svg = Column(Text, nullable=False)
    svg_hash = Column(String(64), nullable=False)
    topology = Column(JSON, nullable=False)
    input_snapshot = Column(JSON, nullable=False)
    generation_status = Column(String(20), nullable=False)
    warnings = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
