from datetime import date

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.db import session_scope
from src.models.school_lunch import Facility, MenuExecution, DailyWorkSchedule
from src.models.route_diagram import KitchenLayout, RouteDiagram, RouteSourceSession
from src.models.school_lunch_timing import TimingProfile
from src.models.implementation_price_ocr_job import ImplementationPriceOcrJob


def get_school_lunch_session(request: Request):
    with session_scope() as session:
        path = request.url.path.removeprefix("/api/school-lunch/route-diagrams")
        schedule_export = request.url.path.startswith("/api/school-lunch/work-schedules/") and request.url.path.endswith("/export")
        if request.method == "GET" and (schedule_export or request.url.path.startswith("/api/school-lunch/route-diagrams/") and (
            path == "/export/month" or path.endswith("/export") or path.startswith("/source-sessions/") and path.endswith("/pdf")
        )):
            from src.services.route_export_snapshot import begin_export_read
            begin_export_read(session)
        yield session


def require_facility(session, facility_id):
    try:
        identifier = int(facility_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "学校IDを指定してください。")
    facility = session.get(Facility, identifier) if identifier > 0 else None
    if facility is None:
        raise HTTPException(404, "指定された学校が見つかりません。")
    return facility


def month_bounds(month):
    try:
        start = date.fromisoformat(month + "-01")
    except (ValueError, TypeError):
        raise HTTPException(422, "対象月をYYYY-MMで指定してください。")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


async def require_school_scope(request: Request, session: Session = Depends(get_school_lunch_session)):
    path = request.url.path.removeprefix("/api/school-lunch")
    if request.method == "GET" and path == "/registered-work":
        return
    if path == "/monthly-overview" or path == "/facilities" or path.startswith("/staff"):
        return
    facility = require_facility(session, request.query_params.get("facility_id"))
    request.state.school_id = facility.id
    request.state.school_name = facility.name
    if request.query_params.get("facility_name", facility.name) != facility.name:
        raise HTTPException(409, "学校IDと学校名が一致しません。")
    if "month" in request.query_params:
        month_bounds(request.query_params["month"])
    job_id = request.path_params.get("job_id")
    if job_id is not None:
        job = session.get(ImplementationPriceOcrJob, job_id)
        if job is None or job.facility_id != facility.id:
            raise HTTPException(404, "選択学校のOCRが見つかりません。未割当の既存資料は帰属確認が必要です。")

    def check_fields(values):
        if not isinstance(values, dict):
            return
        if "facility_id" in values and str(values["facility_id"]) != str(facility.id):
            raise HTTPException(409, "選択学校と保存先が一致しません。")
        if "facility_name" in values and values["facility_name"] != facility.name:
            raise HTTPException(409, "選択学校と資料の学校が一致しません。")

    values = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            values = await request.json()
        except ValueError:
            raise HTTPException(422, "JSONの形式を確認してください。")
    elif "multipart/form-data" in content_type:
        values = dict(await request.form())
    check_fields(values)
    if isinstance(values, dict):
        check_fields(values.get("menu"))
        menus = values.get("menus")
        if isinstance(menus, list):
            for menu in menus:
                check_fields(menu)
        reference = values.get("source_review")
        if isinstance(reference, dict) and reference.get("job_id"):
            job = session.get(ImplementationPriceOcrJob, reference["job_id"])
            if job is None or job.facility_id != facility.id:
                raise HTTPException(404, "選択学校の原本確認が見つかりません。")

    # Every resource addressed by URL, query or input must belong to this school.
    resource_models = {
        "menu_execution_id": MenuExecution, "menu_id": MenuExecution,
        "schedule_id": DailyWorkSchedule, "diagram_id": RouteDiagram,
        "layout_id": KitchenLayout, "profile_id": TimingProfile,
        "session_id": RouteSourceSession,
    }
    for key, model in resource_models.items():
        for source in (request.path_params, request.query_params, values):
            if not isinstance(source, dict) and source is not request.query_params:
                continue
            identifier = source.get(key)
            if identifier is None:
                continue
            try:
                row = session.get(model, int(identifier))
            except (ValueError, TypeError):
                raise HTTPException(422, "対象IDが不正です。")
            if isinstance(row, RouteSourceSession):
                row = session.get(RouteDiagram, row.diagram_id)
            if row is None or row.facility_id != facility.id:
                if request.method == "GET" and path.startswith("/work-schedules/") and path.endswith("/export") and "expected_sources" in request.query_params:
                    raise HTTPException(409, "選択学校の出力対象が欠落または変更されています。")
                raise HTTPException(404, "選択学校の対象データが見つかりません。")
