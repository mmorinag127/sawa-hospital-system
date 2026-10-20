from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from src.db import Base

def now(): return datetime.now(timezone.utc)

class SharedMenu(Base):
    __tablename__='school_lunch_shared_menus'
    id=Column(Integer,primary_key=True); name=Column(String(200),nullable=False,index=True)
    version=Column(Integer,nullable=False,default=0); head_revision_id=Column(Integer,nullable=True)

class SharedMenuRevision(Base):
    __tablename__='school_lunch_shared_menu_revisions'
    __table_args__=(UniqueConstraint('shared_menu_id','number',name='uq_shared_menu_revision_number'),)
    id=Column(Integer,primary_key=True); shared_menu_id=Column(Integer,ForeignKey('school_lunch_shared_menus.id'),nullable=False,index=True)
    number=Column(Integer,nullable=False); parent_revision_id=Column(Integer,ForeignKey('school_lunch_shared_menu_revisions.id'))
    status=Column(String(12),nullable=False); source_month=Column(String(7),nullable=False,index=True)
    provenance=Column(JSON,nullable=False); document=Column(JSON,nullable=False); pattern_associations=Column(JSON,nullable=False)
    change_note=Column(Text,nullable=False,default=''); created_at=Column(DateTime(timezone=True),nullable=False,default=now)

class SharedMenuAdoption(Base):
    __tablename__='school_lunch_shared_menu_adoptions'
    __table_args__=(UniqueConstraint('process_revision_id',name='uq_shared_menu_adoption_process_revision'),)
    id=Column(Integer,primary_key=True); process_revision_id=Column(Integer,ForeignKey('school_lunch_process_revisions.id'),nullable=False)
    shared_menu_revision_id=Column(Integer,ForeignKey('school_lunch_shared_menu_revisions.id'),nullable=False)
    material_mapping=Column(JSON,nullable=False); created_at=Column(DateTime(timezone=True),nullable=False,default=now)
