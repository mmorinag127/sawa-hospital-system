from sqlalchemy import Column, Integer, String, Date, ForeignKey, JSON, LargeBinary, UniqueConstraint
from src.db import Base


class KitchenLayout(Base):
    __tablename__ = "school_lunch_kitchen_layouts"
    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id"), nullable=False)
    name = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    image = Column(LargeBinary, nullable=False)
    locations = Column(JSON, nullable=False, default=dict)


class RouteDiagram(Base):
    __tablename__ = "school_lunch_route_diagrams"
    __table_args__ = (UniqueConstraint("facility_id", "service_date"),)
    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id"), nullable=False)
    service_date = Column(Date, nullable=False)
    layout_id = Column(Integer, ForeignKey("school_lunch_kitchen_layouts.id"), nullable=False)
    layout_version = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    source_versions = Column(JSON, nullable=False)
    routes = Column(JSON, nullable=False)


class RouteSourceSession(Base):
    __tablename__ = "school_lunch_route_source_sessions"
    id = Column(Integer, primary_key=True)
    diagram_id = Column(Integer, ForeignKey("school_lunch_route_diagrams.id"), nullable=False)
    diagram_version = Column(Integer, nullable=False)
    layout_id = Column(Integer, ForeignKey("school_lunch_kitchen_layouts.id"), nullable=False)
    layout_version = Column(Integer, nullable=False)
    source_versions = Column(JSON, nullable=False)
    filename = Column(String(300), nullable=False)
    source_pdf = Column(LargeBinary, nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    page = Column(Integer, nullable=False)
    image = Column(LargeBinary, nullable=False)
    image_sha256 = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="source")
    settings = Column(JSON, nullable=False, default=dict)
    candidates = Column(JSON, nullable=False, default=list)
    confirmation = Column(JSON, nullable=True)
