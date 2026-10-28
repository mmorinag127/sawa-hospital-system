from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event, text
from src.db import Base
from src.shared_menu_catalog_keys import catalog_search_text, menu_identity_key, revision_counts, vocabulary

def now(): return datetime.now(timezone.utc)

class SharedMenu(Base):
    __tablename__='school_lunch_shared_menus'
    id=Column(Integer,primary_key=True); name=Column(String(200),nullable=False,index=True)
    version=Column(Integer,nullable=False,default=0); head_revision_id=Column(Integer,nullable=True)
    name_identity = Column(String(64), nullable=False, index=True)
    search_name = Column(Text, nullable=False)

class SharedMenuRevision(Base):
    __tablename__='school_lunch_shared_menu_revisions'
    __table_args__=(UniqueConstraint('shared_menu_id','number',name='uq_shared_menu_revision_number'),)
    id=Column(Integer,primary_key=True); shared_menu_id=Column(Integer,ForeignKey('school_lunch_shared_menus.id'),nullable=False,index=True)
    number=Column(Integer,nullable=False); parent_revision_id=Column(Integer,ForeignKey('school_lunch_shared_menu_revisions.id'))
    status=Column(String(12),nullable=False); source_month=Column(String(7),nullable=False,index=True)
    provenance=Column(JSON,nullable=False); document=Column(JSON,nullable=False); pattern_associations=Column(JSON,nullable=False)
    change_note=Column(Text,nullable=False,default=''); created_at=Column(DateTime(timezone=True),nullable=False,default=now)
    ingredient_count = Column(Integer, nullable=False)
    step_count = Column(Integer, nullable=False)
    question_count = Column(Integer, nullable=False)
    unanswered_count = Column(Integer, nullable=False)

class SharedMenuAdoption(Base):
    __tablename__='school_lunch_shared_menu_adoptions'
    __table_args__=(UniqueConstraint('process_revision_id',name='uq_shared_menu_adoption_process_revision'),)
    id=Column(Integer,primary_key=True); process_revision_id=Column(Integer,ForeignKey('school_lunch_process_revisions.id'),nullable=False)
    shared_menu_revision_id=Column(Integer,ForeignKey('school_lunch_shared_menu_revisions.id'),nullable=False)
    material_mapping=Column(JSON,nullable=False); created_at=Column(DateTime(timezone=True),nullable=False,default=now)


class SharedMenuVocabulary(Base):
    __tablename__ = 'school_lunch_shared_menu_vocabulary'
    kind = Column(String(12), primary_key=True)
    value = Column(String(500), primary_key=True)
    first_revision_id = Column(Integer, ForeignKey('school_lunch_shared_menu_revisions.id'), nullable=False)


@event.listens_for(SharedMenu, 'before_insert')
@event.listens_for(SharedMenu, 'before_update')
def _name_identity(_mapper, _connection, target):
    target.name_identity = menu_identity_key(target.name)
    target.search_name = catalog_search_text(target.name)


@event.listens_for(SharedMenuRevision, 'after_insert')
@event.listens_for(SharedMenuRevision, 'after_update')
def _retain_vocabulary(_mapper, connection, target):
    rows = vocabulary(target.document, target.id)
    if rows:
        connection.execute(text(
            'INSERT INTO school_lunch_shared_menu_vocabulary (kind, value, first_revision_id) '
            'VALUES (:kind, :value, :first_revision_id) ON CONFLICT (kind, value) DO NOTHING'
        ), rows)


@event.listens_for(SharedMenuRevision, 'before_insert')
@event.listens_for(SharedMenuRevision, 'before_update')
def _revision_counts(_mapper, _connection, target):
    for key, value in revision_counts(target.document, target.provenance).items():
        setattr(target, key, value)
