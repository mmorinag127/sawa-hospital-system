from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.auth import require_role
from src.services import order_form_service


router = APIRouter()


@router.get("/order-forms/patterns", dependencies=[Depends(require_role("operator"))])
def list_order_form_patterns():
    return {"patterns": order_form_service.list_order_form_patterns()}


@router.post("/order-forms/generate", dependencies=[Depends(require_role("operator"))])
def generate_order_form(
    facility_id: str,
    month_id: str,
    pattern_id: str | None = None,
):
    try:
        output_path = order_form_service.build_order_form_excel(
            facility_id=facility_id,
            month_id=month_id,
            pattern_id=pattern_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to generate order form: {exc}") from exc
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_path.name,
    )
