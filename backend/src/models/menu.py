from datetime import datetime

from sqlalchemy import Column, String, Float, ForeignKey, Date, DateTime, Integer, Boolean
from sqlalchemy.orm import relationship

from src.db import Base


class WeeklyMenu(Base):
    __tablename__ = "weekly_menus"

    id = Column(String, primary_key=True)
    week_start = Column(Date, nullable=True)
    filename = Column(String, nullable=True)
    items = relationship("MenuItem", back_populates="menu", cascade="all, delete-orphan")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(String, primary_key=True)
    weekly_menu_id = Column(String, ForeignKey("weekly_menus.id"), nullable=False)
    name = Column(String, nullable=False)
    unit_type = Column(String, nullable=True)
    qty_per_serving = Column(Float, nullable=True)
    temp_type = Column(String, nullable=True)
    daypart = Column(String, nullable=True)
    category = Column(String, nullable=True)
    facility_override = Column(String, nullable=True)

    menu = relationship("WeeklyMenu", back_populates="items")


class MenuMaster(Base):
    __tablename__ = "menu_masters"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=False, index=True, unique=True)
    unit_type = Column(String, nullable=True)
    qty_per_serving = Column(Float, nullable=True)
    bag_max_qty = Column(Float, nullable=True)
    bag_max_unit = Column(String, nullable=True)
    temp_type = Column(String, nullable=True)
    daypart = Column(String, nullable=True)
    category = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    overrides = relationship(
        "MenuFacilityOverride",
        back_populates="menu",
        cascade="all, delete-orphan",
    )


class MenuFacilityOverride(Base):
    __tablename__ = "menu_facility_overrides"

    id = Column(String, primary_key=True)
    menu_master_id = Column(String, ForeignKey("menu_masters.id"), nullable=False)
    facility_id = Column(String, nullable=False)
    unit_type = Column(String, nullable=True)
    qty_per_serving = Column(Float, nullable=True)
    bag_max_qty = Column(Float, nullable=True)
    bag_max_unit = Column(String, nullable=True)
    temp_type = Column(String, nullable=True)
    daypart = Column(String, nullable=True)
    category = Column(String, nullable=True)

    menu = relationship("MenuMaster", back_populates="overrides")


class MonthlyMenu(Base):
    __tablename__ = "monthly_menus"

    id = Column(String, primary_key=True)
    month_start = Column(Date, nullable=True)
    filename = Column(String, nullable=True)
    items = relationship("MonthlyMenuItem", back_populates="menu", cascade="all, delete-orphan")
    entries = relationship(
        "MonthlyMenuEntry",
        back_populates="menu",
        cascade="all, delete-orphan",
    )


class MonthlyMenuItem(Base):
    __tablename__ = "monthly_menu_items"

    id = Column(String, primary_key=True)
    monthly_menu_id = Column(String, ForeignKey("monthly_menus.id"), nullable=False)
    name = Column(String, nullable=False)
    unit_type = Column(String, nullable=True)
    qty_per_serving = Column(Float, nullable=True)
    temp_type = Column(String, nullable=True)
    daypart = Column(String, nullable=True)
    category = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    facility_override = Column(String, nullable=True)

    menu = relationship("MonthlyMenu", back_populates="items")


class MonthlyMenuEntry(Base):
    __tablename__ = "monthly_menu_entries"

    id = Column(String, primary_key=True)
    monthly_menu_id = Column(String, ForeignKey("monthly_menus.id"), nullable=False)
    menu_date = Column(Date, nullable=False)
    daypart = Column(String, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    slot_index = Column(Integer, nullable=True)

    menu = relationship("MonthlyMenu", back_populates="entries")


class MenuRule(Base):
    __tablename__ = "menu_rules"

    id = Column(String, primary_key=True)
    rule_type = Column(String, nullable=False)
    match_type = Column(String, nullable=True)
    menu_pattern = Column(String, nullable=True)
    facility_id = Column(String, nullable=True)
    daypart = Column(String, nullable=True)
    category = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    unit_type = Column(String, nullable=True)
    qty_per_serving = Column(Float, nullable=True)
    priority = Column(Integer, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
