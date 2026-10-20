from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.school_lunch import StaffMember
from src.services.school_lunch_common_service import SchoolLunchConflict, SchoolLunchEntityNotFound


def serialize(member):
    return {"id": member.id, "name": member.name, "active": member.active, "version": member.version}


def list_staff(session: Session):
    return [serialize(member) for member in session.scalars(select(StaffMember).order_by(StaffMember.id))]


def create_staff(session: Session, name: str):
    member = StaffMember(name=name, active=True, version=1)
    session.add(member)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise SchoolLunchConflict("同じ名前の担当者が登録されています。") from exc
    return serialize(member)


def update_staff(session: Session, member_id: int, payload: dict):
    if session.get(StaffMember, member_id) is None:
        raise SchoolLunchEntityNotFound("担当者が見つかりません。")
    try:
        result = session.execute(
            update(StaffMember)
            .where(StaffMember.id == member_id, StaffMember.version == payload["version"])
            .values(name=payload["name"], active=payload["active"], version=payload["version"] + 1)
            .execution_options(synchronize_session=False)
        )
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise SchoolLunchConflict("同じ名前の担当者が登録されています。") from exc
    if result.rowcount != 1:
        raise SchoolLunchConflict("担当者が更新されています。再読み込みしてください。")
    session.expire_all()
    return serialize(session.get(StaffMember, member_id))
