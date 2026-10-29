from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.school_scope import get_school_lunch_session
from src.schemas.school_work_inbox import RegisteredWorkResponse, RegisteredWorkIndex
from src.services.school_work_inbox import registered_work_page, registered_work_index


# Mounted by the school common router, retaining its role and scope dependencies.
router = APIRouter()


@router.get('/registered-work-index', response_model=RegisteredWorkIndex)
def get_registered_work_index(session: Session = Depends(get_school_lunch_session)):
    return registered_work_index(session)


@router.get('/registered-work', response_model=RegisteredWorkResponse)
def get_registered_work(
    session: Session = Depends(get_school_lunch_session),
    facility_id: int | None = Query(default=None, gt=0),
    month: str | None = Query(default=None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    work_item: str | None = Query(default=None, min_length=1, max_length=200),
    unassigned_only: bool = False,
    after_id: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=100),
):
    return registered_work_page(session, facility_id=facility_id, month=month,
        work_item=work_item, unassigned_only=unassigned_only, after_id=after_id, limit=limit)
