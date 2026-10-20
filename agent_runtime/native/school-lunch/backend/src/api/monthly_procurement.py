from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.encoders import jsonable_encoder
import hashlib
import json
from pydantic import BaseModel, Field, StringConstraints
from starlette.responses import Response

from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.services import monthly_procurement_service as service
from src.services.workbook_pdf_service import OutputFormat, workbook_response
from src.api.monthly_procurement_contract import MonthlyProcurementResponse, EXPORT_RESPONSE

router = APIRouter(prefix="/api/school-lunch/monthly-procurement", dependencies=[Depends(require_role("operator"))])
NonNegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=18, decimal_places=6)]
Month = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]


class Incoming(BaseModel):
    date: date
    quantity: NonNegative


class IngredientInput(BaseModel):
    ingredient_id: int = Field(gt=0)
    opening_stock: NonNegative | None = None
    unit_price: NonNegative | None = None
    order_increment: Annotated[Decimal, Field(gt=0, allow_inf_nan=False, max_digits=18, decimal_places=6)] | None = None
    price_unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)] | None = None
    supplier: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    incoming: list[Incoming] = Field(default_factory=list, max_length=1000)


class MonthlyInput(BaseModel):
    version: int = Field(ge=0)
    menu_versions: dict[str, str]
    ingredients: list[IngredientInput] = Field(max_length=5000)


@router.get("", responses={200: {"model": MonthlyProcurementResponse}})
def read(facility_id: int = Query(gt=0), month: Month = Query(), session=Depends(get_school_lunch_session)):
    return service.compute(session, facility_id, month)


@router.put("", responses={200: {"model": MonthlyProcurementResponse}})
def save(payload: MonthlyInput, facility_id: int = Query(gt=0), month: Month = Query(), session=Depends(get_school_lunch_session)):
    return service.save(session, facility_id, month, payload.model_dump(mode="json"))


@router.get("/export", response_class=Response, responses=EXPORT_RESPONSE)
def export(facility_id: int = Query(gt=0), month: Month = Query(), output: OutputFormat = "xlsx", expected_fingerprint: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"), session=Depends(get_school_lunch_session)):
    result = service.compute(session, facility_id, month)
    if expected_fingerprint is not None:
        encoded=json.dumps(jsonable_encoder(result),sort_keys=True,ensure_ascii=False,separators=(",", ":"))
        if hashlib.sha256(encoded.encode()).hexdigest()!=expected_fingerprint:
            raise HTTPException(status_code=409,detail="確認後に献立・在庫・単価の計算結果が変わりました。再確認してください。")
    return workbook_response(service.excel(result), result['facility']['name'] + '-' + month + '-価格在庫発注', output)
