from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, String, Time, UniqueConstraint

from src.db import Base


class TimingProfile(Base):
    __tablename__ = "school_lunch_timing_profiles"
    __table_args__ = (
        UniqueConstraint("facility_id", "name", name="uq_lunch_timing_profile_name"),
        CheckConstraint("version >= 1", name="ck_lunch_timing_version"),
        CheckConstraint("length(trim(name)) > 0", name="ck_lunch_timing_name"),
        CheckConstraint("plating_end > plating_start", name="ck_lunch_timing_plating"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(100), nullable=False)
    plating_start = Column(Time, nullable=False)
    plating_end = Column(Time, nullable=False)
    timeline_ticks = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False)
