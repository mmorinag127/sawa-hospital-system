from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import require_role
from src.api.output_history_contract import (
    OutputHistoryResponse, OrderIdQuery, WorkflowRevisionQuery, PageQuery, LimitQuery,
)
from src.db import SessionLocal
from src.services.output_history_service import list_output_history, OutputHistoryNotFound

router = APIRouter()


@router.get("/history", dependencies=[Depends(require_role("operator"))], response_model=None,
            responses={200: {"model": OutputHistoryResponse}})
def get_output_history(order_id: OrderIdQuery, expected_workflow_revision: WorkflowRevisionQuery,
                       page: PageQuery, limit: LimitQuery, before_id: str | None = None,
                       validate_records: bool = True, record_id: str | None = None):
    try:
        with SessionLocal() as session:
            return list_output_history(session, order_id=order_id,
                                       expected_workflow_revision=expected_workflow_revision,
                                       page=page, limit=limit, before_id=before_id,
                                       validate_records=validate_records, record_id=record_id)
    except OutputHistoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
