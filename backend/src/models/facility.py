from sqlalchemy import Column, String, ForeignKey, JSON
from sqlalchemy.orm import relationship

from src.db import Base


class Facility(Base):
    __tablename__ = "facilities"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    areas = relationship("FacilityArea", back_populates="facility", cascade="all, delete-orphan")
    config = relationship("FacilityConfig", uselist=False, back_populates="facility", cascade="all, delete-orphan")


class FacilityArea(Base):
    __tablename__ = "facility_areas"

    id = Column(String, primary_key=True)
    facility_id = Column(String, ForeignKey("facilities.id"), primary_key=True, nullable=False)
    name = Column(String, nullable=False)

    facility = relationship("Facility", back_populates="areas")


class FacilityConfig(Base):
    __tablename__ = "facility_configs"

    facility_id = Column(String, ForeignKey("facilities.id"), primary_key=True)
    config_json = Column(JSON, nullable=True)

    facility = relationship("Facility", back_populates="config")
