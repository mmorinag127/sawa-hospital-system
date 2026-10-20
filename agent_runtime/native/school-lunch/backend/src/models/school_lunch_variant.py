from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, String

from src.db import Base


class MenuVariant(Base):
    __tablename__ = "school_lunch_menu_variants"
    __table_args__ = (CheckConstraint("kind IN ('reuse', 'support', 'allergy')", name="ck_menu_variant_kind"),)

    id = Column(Integer, primary_key=True)
    source_menu_id = Column(Integer, ForeignKey("school_lunch_menu_executions.id", ondelete="RESTRICT"), nullable=False)
    target_menu_id = Column(Integer, ForeignKey("school_lunch_menu_executions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_version = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False)
    instruction = Column(String(2000), nullable=False)
    source_snapshot = Column(JSON, nullable=False)
