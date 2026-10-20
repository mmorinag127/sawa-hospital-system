from datetime import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.services import school_lunch_timing_service as service
from src.services.school_lunch_common_service import SchoolLunchConflict, SchoolLunchEntityNotFound

from src.api.school_scope import require_school_scope
from src.api.school_lunch_timing_contract import CalculationResponse, TimingProfileResponse

router = APIRouter(prefix="/api/school-lunch/timing-profiles", dependencies=[Depends(require_role("operator")), Depends(require_school_scope)])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tick = Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]


class ProfileWrite(BaseModel):
    facility_name: Name
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    plating_start: time
    plating_end: time
    timeline_ticks: list[Tick] = Field(min_length=2)

    @model_validator(mode="after")
    def valid_times(self):
        if any(t.second or t.microsecond or t.tzinfo for t in (self.plating_start, self.plating_end)):
            raise ValueError("時刻は時・分で入力してください。")
        if self.plating_end <= self.plating_start:
            raise ValueError("盛付終了は同日の盛付開始より後にしてください。")
        if any(a >= b for a, b in zip(self.timeline_ticks, self.timeline_ticks[1:])):
            raise ValueError("時刻目盛は同日の時刻を重複なく昇順で入力してください。")
        return self


class ProfileUpdate(ProfileWrite):
    version: int = Field(ge=1)


class TaskOffset(BaseModel):
    key: Name
    duration_minutes: int = Field(gt=0, strict=True)
    offset_before_plating_minutes: int = Field(strict=True)


class Calculation(BaseModel):
    facility_name: Name
    profile_id: int = Field(gt=0)
    profile_version: int = Field(ge=1)
    tasks: list[TaskOffset]

    @model_validator(mode="after")
    def unique_keys(self):
        if len({task.key for task in self.tasks}) != len(self.tasks):
            raise ValueError("作業キーが重複しています。")
        return self


def run(operation, *args):
    try:
        return operation(*args)
    except SchoolLunchEntityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SchoolLunchConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", responses={200: {"model": list[TimingProfileResponse]}})
def list_profiles(facility_id: int = Query(gt=0), session: Session = Depends(get_school_lunch_session)):
    return service.list_profiles(session, facility_id)


@router.post("", status_code=201, responses={201: {"model": TimingProfileResponse}})
def create_profile(payload: ProfileWrite, session: Session = Depends(get_school_lunch_session)):
    return run(service.save_profile, session, payload.model_dump())


@router.post("/calculate", responses={200: {"model": CalculationResponse}})
def calculate(payload: Calculation, session: Session = Depends(get_school_lunch_session)):
    return run(service.calculate, session, payload.model_dump())


@router.put("/{profile_id}", responses={200: {"model": TimingProfileResponse}})
def update_profile(profile_id: int, payload: ProfileUpdate, session: Session = Depends(get_school_lunch_session)):
    return run(service.save_profile, session, payload.model_dump(), profile_id)
