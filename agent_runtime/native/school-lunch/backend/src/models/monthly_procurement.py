from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, String, UniqueConstraint

from src.db import Base


class MonthlyProcurement(Base):
    __tablename__ = "school_lunch_monthly_procurement"
    __table_args__ = (
        UniqueConstraint("facility_id", "month", name="uq_monthly_procurement_period"),
        CheckConstraint("version >= 1", name="ck_monthly_procurement_version"),
    )

    id = Column(Integer, primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False)
    month = Column(String(7), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    inputs = Column(JSON, nullable=False)
    menu_versions = Column(JSON, nullable=False)
