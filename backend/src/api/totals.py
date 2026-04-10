from datetime import datetime, date

from fastapi import APIRouter, HTTPException, Depends

from src.api.auth import require_role
from src.services import total_service

router = APIRouter()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


@router.get("/totals", dependencies=[Depends(require_role("operator"))])
def get_totals(
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_order_refs: bool = False,
):
    if date:
        parsed = _parse_date(date)
        if not parsed:
            raise HTTPException(status_code=400, detail="invalid date")
        date_from = date_to = date
    parsed_from = _parse_date(date_from)
    parsed_to = _parse_date(date_to)
    if (date_from and not parsed_from) or (date_to and not parsed_to):
        raise HTTPException(status_code=400, detail="invalid date range")
    rows = total_service.build_totals(parsed_from, parsed_to, include_order_refs=include_order_refs)
    return {"rows": rows, "date_from": date_from, "date_to": date_to}
