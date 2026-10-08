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
                       page: PageQuery, limit: LimitQuery):
    try:
        with SessionLocal() as session:
            return list_output_history(session, order_id=order_id,
                                       expected_workflow_revision=expected_workflow_revision,
                                       page=page, limit=limit)
    except OutputHistoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
