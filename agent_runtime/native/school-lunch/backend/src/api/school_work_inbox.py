from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.school_scope import get_school_lunch_session
from src.schemas.school_work_inbox import RegisteredWorkResponse
from src.services.school_work_inbox import registered_work


# Mounted by the school common router, retaining its role and scope dependencies.
router = APIRouter()


@router.get('/registered-work', response_model=RegisteredWorkResponse)
def get_registered_work(
    session: Session = Depends(get_school_lunch_session),
    facility_id: int | None = Query(default=None, gt=0),
    month: str | None = Query(default=None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
):
    return registered_work(session, facility_id=facility_id, month=month)
