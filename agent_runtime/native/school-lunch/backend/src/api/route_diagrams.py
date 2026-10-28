from datetime import date
from io import BytesIO
import base64
import re
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, ValidationError, field_validator
from typing import Annotated, Literal
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session, load_only
from starlette.responses import StreamingResponse
from src.services.workbook_pdf_service import OutputFormat, workbook_response
from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.models.school_lunch import Facility, MenuExecution
from src.models.route_diagram import KitchenLayout, RouteDiagram, RouteSourceSession
from src.services import route_diagram_service as service
from src.services.kitchen_layout_source import render_pdf_page
from src.services.route_export_snapshot import begin_export_read, freeze_row
from src.api.route_diagram_contract import RouteLayoutResponse, RouteDiagramResponse, RouteSourceResponse, RouteSourcePageResponse

from src.api.school_scope import require_school_scope

router = APIRouter(prefix="/api/school-lunch/route-diagrams", dependencies=[Depends(require_role("operator")), Depends(require_school_scope)])


class LocationsWrite(BaseModel):
    version: int = Field(ge=1)
    locations: dict[str, tuple[float, float]]

    @field_validator("locations")
    @classmethod
    def check_locations(cls, value):
        if len(value) > 300 or any(not key.strip() or len(key) > 200 or not all(0 <= n <= 1 for n in point) for key, point in value.items()):
            raise ValueError("場所名と図面内の座標を指定してください")
        return value


class GenerateWrite(BaseModel):
    facility_id: int
    service_date: date
    layout_id: int
    version: int = Field(ge=0)
    replace_confirmed_source: bool = False


class SegmentState(BaseModel):
    food_category: Literal["main", "side_green", "side_orange", "dessert_salad", "soup"] | None = None
    raw_meat: bool | None = None
    heated: bool | None = None


class LabelModes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Literal["automatic", "manual"] | None = None
    map_label: Literal["automatic", "manual"] | None = None
    label_position: Literal["automatic", "manual"] | None = None


class RouteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    label: str = Field(min_length=1, max_length=400)
    map_label: str | None = Field(default=None, min_length=1, max_length=40)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    locations: list[str] = Field(max_length=100)
    styles: list[str | None] = Field(max_length=100)
    points: list[tuple[float, float]] | None = Field(default=None, min_length=2, max_length=100)
    label_position: tuple[float, float] | None = None
    segment_states: list[SegmentState] | None = Field(default=None, max_length=99)
    source_provenance: dict | None = None
    process_source: dict[str, int] | None = None
    label_modes: LabelModes | None = None

    @field_validator("points", "label_position")
    @classmethod
    def check_points(cls, value, info):
        if value is None:
            return value
        points = [value] if info.field_name == "label_position" else value
        if points is not None and any(not all(0 <= n <= 1 for n in point) for point in points):
            raise ValueError("図面内の座標（0〜1）を指定してください")
        return value

    @field_validator("styles")
    @classmethod
    def check_styles(cls, value):
        if any(style not in ("solid", "dashed", None, "") for style in value):
            raise ValueError("実線または破線を指定してください")
        return [style if style else None for style in value]


class DiagramWrite(BaseModel):
    version: int = Field(ge=1)
    routes: list[RouteWrite] = Field(max_length=1000)

    @field_validator("routes")
    @classmethod
    def unique_map_labels(cls, routes):
        if any(route.map_label is not None and not route.map_label.strip() for route in routes):
            raise ValueError("図中の短縮ラベルは空白・重複を避けてください")
        labels = [route.map_label.strip() for route in routes if route.map_label is not None and not (route.label_modes and route.label_modes.map_label == "automatic")]
        if any(not label for label in labels) or len(labels) != len(set(labels)):
            raise ValueError("図中の短縮ラベルは空白・重複を避けてください")
        return routes


def layout_data(layout, *, include_image=True):
    return {"id": layout.id, "facility_id": layout.facility_id, "name": layout.name, "version": layout.version, "locations": layout.locations, "image": "data:image/png;base64," + base64.b64encode(layout.image).decode() if include_image else None}


def diagram_data(session, diagram, *, blockers=None, source_views=None):
    from src.services.route_source_service import modified
    return {"id": diagram.id, "facility_id": diagram.facility_id, "service_date": str(diagram.service_date), "layout_id": diagram.layout_id, "layout_version": diagram.layout_version, "version": diagram.version, "source_versions": diagram.source_versions, "routes": [service.public_route(r) for r in diagram.routes], "source_modifications": {str(r["id"]): modified(session,r, sources=source_views) for r in diagram.routes if r.get("source_provenance")}, "blockers": service.blockers(session, diagram) if blockers is None else blockers}


@router.get("/layouts", responses={200: {"model": list[RouteLayoutResponse]}})
def layouts(facility_id: int, session: Session = Depends(get_school_lunch_session), summary: bool = True):
    query = session.query(KitchenLayout).filter_by(facility_id=facility_id).order_by(KitchenLayout.id)
    if summary:
        query = query.options(load_only(KitchenLayout.id, KitchenLayout.facility_id, KitchenLayout.name,
                                       KitchenLayout.version, KitchenLayout.locations))
    return [layout_data(row, include_image=not summary) for row in query]


@router.get("/layouts/{layout_id}", responses={200: {"model": RouteLayoutResponse}})
def read_layout(layout_id: int, facility_id: int, version: int, session: Session = Depends(get_school_lunch_session)):
    row = session.query(KitchenLayout).filter_by(id=layout_id, facility_id=facility_id).one_or_none()
    if row is None:
        raise HTTPException(404, "選択された学校の図面が見つかりません。")
    if row.version != version:
        raise HTTPException(409, "図面が変更されています。一覧を再読み込みしてください。")
    return layout_data(row)


@router.post("/layouts")
async def create_layout(facility_id: int = Form(...), name: str = Form(...), file: UploadFile = File(...), pdf_page: int | None = Form(default=None), session: Session = Depends(get_school_lunch_session)):
    if not session.get(Facility, facility_id) or not name.strip() or len(name) > 200:
        raise HTTPException(422, "学校と図面名を指定してください")
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "図面は10MB以下にしてください")
    if raw.startswith(b"%PDF-") or str(file.filename).lower().endswith(".pdf"):
        try:
            raw = await run_in_threadpool(render_pdf_page, raw, pdf_page)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    try:
        image = Image.open(BytesIO(raw))
        if image.width * image.height > 20_000_000:
            raise HTTPException(413, "図面の画素数が大きすぎます")
        image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "PNGまたはJPEGの厨房図面を指定してください") from exc
    row = KitchenLayout(facility_id=facility_id, name=name.strip(), image=output.getvalue(), locations={})
    session.add(row)
    session.flush()
    return layout_data(row)


@router.put("/layouts/{layout_id}", responses={200: {"model": RouteLayoutResponse}})
def save_layout(layout_id: int, body: LocationsWrite, session: Session = Depends(get_school_lunch_session)):
    row = session.query(KitchenLayout).filter_by(id=layout_id).with_for_update().one_or_none()
    if not row:
        raise HTTPException(404, "図面がありません")
    if row.version != body.version:
        raise HTTPException(409, "別の変更があります。再読み込みしてください")
    row.locations = {key: list(value) for key, value in body.locations.items()}
    row.version += 1
    session.flush()
    return layout_data(row)


@router.get("", responses={200: {"model": list[RouteDiagramResponse]}})
def list_diagrams(facility_id: int, month: str, session: Session = Depends(get_school_lunch_session)):
    start, end = month_range(month)
    rows = session.query(RouteDiagram).filter(RouteDiagram.facility_id == facility_id, RouteDiagram.service_date >= start, RouteDiagram.service_date < end).order_by(RouteDiagram.service_date).all()
    from src.services.route_source_service import source_views
    sources = source_views(session, rows)
    blockers = service.blockers_for_diagrams(session, rows, source_views=sources)
    return [diagram_data(session, row, blockers=blockers[row.id], source_views=sources) for row in rows]


def month_range(month):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError()
        start = date.fromisoformat(month + "-01")
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        return start, end
    except ValueError as exc:
        raise HTTPException(422, "対象月をYYYY-MMで指定してください") from exc


@router.post("/generate", responses={200: {"model": RouteDiagramResponse}})
def generate(body: GenerateWrite, session: Session = Depends(get_school_lunch_session)):
    return diagram_data(session, service.generate(session, body.facility_id, body.service_date, body.layout_id, body.version, body.replace_confirmed_source))


@router.put("/{diagram_id}", responses={200: {"model": RouteDiagramResponse}})
def save_diagram(diagram_id: int, body: DiagramWrite, session: Session = Depends(get_school_lunch_session)):
    row = session.query(RouteDiagram).filter_by(id=diagram_id).with_for_update().one_or_none()
    if not row:
        raise HTTPException(404, "動線図がありません")
    if row.version != body.version:
        raise HTTPException(409, "別の変更があります。再読み込みしてください")
    if service.versions(service.menus(session, row.facility_id, row.service_date)) != row.source_versions or session.get(KitchenLayout, row.layout_id).version != row.layout_version:
        raise HTTPException(409, "献立または図面が変更されています。再生成してください")
    if sorted(route.id for route in body.routes) != sorted(route["id"] for route in row.routes):
        raise HTTPException(422, "材料の追加・削除は献立で行い、再生成してください")
    if any(len(route.styles) != (len(route.points)-1 if route.points is not None else len(route.locations)) for route in body.routes):
        raise HTTPException(422, "個別経路は線分数（点数−1）、登録場所の経路は場所数と線種の数を一致させてください")
    if any(route.segment_states is not None and len(route.segment_states) != len(route.points if route.points is not None else route.locations) - 1 for route in body.routes):
        raise HTTPException(422, "線分と色・加熱設定の数が一致しません")
    existing = {r["id"]: r for r in row.routes}
    for route in body.routes:
        if route.process_source is not None and route.process_source != existing[route.id].get("process_source"):
            raise HTTPException(422, "回答した工程の出典は動線再生成でのみ更新できます")
        route.process_source = existing[route.id].get("process_source")
        if route.source_provenance != existing[route.id].get("source_provenance"):
            raise HTTPException(422, "原本候補の出典は専用の確認・適用画面でのみ登録できます")
    # Compare the same JSON values both immediately and after DB reload;
    # Pydantic's Python-mode coordinate tuples otherwise differ from JSON lists.
    routes = [service.saved_route_labels(route.model_dump(mode="json", exclude_none=True), existing[route.id]) for route in body.routes]
    service.apply_automatic_label_choices(session, row, routes, existing)
    if service.label_issues(routes):
        raise HTTPException(422, service.label_issues(routes)[0])
    row.routes = routes
    row.version += 1
    session.flush()
    return diagram_data(session, row)


class ExportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt = Field(gt=0)
    version: StrictInt = Field(gt=0)
    facility_id: StrictInt = Field(gt=0)
    service_date: date
    layout_id: StrictInt = Field(gt=0)
    layout_version: StrictInt = Field(gt=0)


def check_export_sources(session, rows, expected_sources):
    if expected_sources is None:
        return {layout.id: freeze_row(layout) for layout in session.query(KitchenLayout).filter(KitchenLayout.id.in_({row.layout_id for row in rows})).populate_existing().all()}
    try:
        sources = TypeAdapter(list[ExportSource]).validate_json(expected_sources)
    except ValidationError as exc:
        raise HTTPException(422, "出力対象の版を明示してください") from exc
    if len({source.id for source in sources}) != len(sources) or len(sources) != len(rows):
        raise HTTPException(409, "動線図の選択が変更されています。再読み込みしてください")
    recorded = {source.id: source for source in sources}
    for row in rows:
        source = recorded.get(row.id)
        if source is None or any(getattr(row, key) != getattr(source, key) for key in ExportSource.model_fields):
            raise HTTPException(409, "動線図の版が変更されています。再読み込みしてください")
    layouts = session.query(KitchenLayout).filter(KitchenLayout.id.in_({row.layout_id for row in rows})).order_by(KitchenLayout.id).populate_existing().all()
    by_id = {layout.id: layout for layout in layouts}
    for row in rows:
        layout = by_id.get(row.layout_id)
        if layout is None or layout.facility_id != row.facility_id or layout.version != row.layout_version:
            raise HTTPException(409, "図面の版が変更されています。再読み込みしてください")
    return {layout.id: freeze_row(layout) for layout in layouts}


@router.get("/export/month")
def export_month(facility_id: int, month: str, output: OutputFormat = "xlsx", session: Session = Depends(get_school_lunch_session), expected_sources: str | None = None):
    begin_export_read(session)
    start, end = month_range(month)
    query = session.query(RouteDiagram).filter(RouteDiagram.facility_id == facility_id, RouteDiagram.service_date >= start, RouteDiagram.service_date < end).order_by(RouteDiagram.service_date)
    rows = query.populate_existing().all()
    layouts = check_export_sources(session, rows, expected_sources)
    expected = {r.service_date for r in session.query(MenuExecution).filter(MenuExecution.facility_id == facility_id, MenuExecution.service_date >= start, MenuExecution.service_date < end)}
    missing = expected - {r.service_date for r in rows}
    if missing:
        raise HTTPException(409, {"missing_dates": sorted(str(day) for day in missing), "message": "未生成の日があります"})
    return workbook_response(service.export(session, rows, layouts=layouts), f"routes-{month}", output)


@router.get("/{diagram_id}/export")
def export_day(diagram_id: int, output: OutputFormat = "xlsx", session: Session = Depends(get_school_lunch_session), expected_sources: str | None = None):
    begin_export_read(session)
    row = session.query(RouteDiagram).filter(RouteDiagram.id == diagram_id).populate_existing().one_or_none()
    if not row:
        raise HTTPException(404, "動線図がありません")
    layouts = check_export_sources(session, [row], expected_sources)
    return workbook_response(service.export(session, [row], layouts=layouts), f"routes-{row.service_date}", output)


class SourceExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = Field(ge=1)
    roi: tuple[Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)]]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    hue_tolerance: StrictInt = Field(ge=1, le=40)
    min_saturation: StrictInt = Field(ge=1, le=255)
    min_value: StrictInt = Field(ge=0, le=255)
    min_length: StrictInt = Field(ge=5, le=2000)
    max_gap: StrictInt = Field(ge=0, le=100)
    threshold: StrictInt = Field(ge=5, le=500)

    @field_validator("roi")
    @classmethod
    def check_roi(cls, value):
        if not all(0 <= n <= 1 for n in value) or value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("左上・右下を図面内の0〜1で明示してください")
        return value


class CandidateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt = Field(ge=1)
    reverse: StrictBool


class SourceConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_id: StrictInt = Field(ge=1)
    selection: list[CandidateSelection] = Field(min_length=1, max_length=50)
    destination_roi: tuple[Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)]]
    styles: list[Literal["solid", "dashed"]] = Field(min_length=1, max_length=99)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    reviewed: StrictBool
    alignment_reviewed: StrictBool
    connectors_reviewed: StrictBool

    @field_validator("destination_roi")
    @classmethod
    def check_roi(cls, value):
        return SourceExtract.check_roi(value)


def source_data(row):
    from src.services.route_source_service import source_content
    _, image = source_content(row)
    return {"id": row.id, "diagram_id": row.diagram_id, "version": row.version, "status": row.status,
            "filename": row.filename, "source_sha256": row.source_sha256, "image_sha256": row.image_sha256,
            "page": row.page, "image": "data:image/png;base64,"+base64.b64encode(image).decode(),
            "settings": row.settings, "candidates": row.candidates, "confirmation": row.confirmation,
            "blockers": [] if row.status == "confirmed" else ["未確定です。枠線も含む候補から材料・順番・向き・位置合わせ・線種を確認してください" if row.candidates else "線候補がありません。読取範囲・色・設定を原本で確認してください"]}


def source_row(session, session_id):
    row = session.query(RouteSourceSession).filter_by(id=session_id).with_for_update().one_or_none()
    if not row:
        raise HTTPException(404, "原本読取がありません")
    return row


@router.get("/source-sessions/list", responses={200: {"model": list[RouteSourceResponse]}})
def list_sources(diagram_id: int, session: Session = Depends(get_school_lunch_session), *,
                 before_id: Annotated[int | None, Query(ge=1)] = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 25):
    query = session.query(RouteSourceSession).filter_by(diagram_id=diagram_id)
    if before_id is not None:
        query = query.filter(RouteSourceSession.id < before_id)
    return [source_data(row) for row in query.order_by(RouteSourceSession.id.desc()).limit(limit)]


@router.get("/source-sessions/page", responses={200: {"model": RouteSourcePageResponse}})
def source_page(diagram_id: int, before_id: int | None = None, session: Session = Depends(get_school_lunch_session)):
    fields = ("id", "diagram_id", "version", "status", "filename", "source_sha256", "image_sha256", "page")
    query = session.query(*(getattr(RouteSourceSession, name) for name in fields)).filter_by(diagram_id=diagram_id)
    if before_id is not None:
        if before_id <= 0:
            raise HTTPException(422, "原本一覧のページ指定が不正です")
        query = query.filter(RouteSourceSession.id < before_id)
    rows = query.order_by(RouteSourceSession.id.desc()).limit(26).all()
    return {"items": [{name: getattr(row, name) for name in fields} for row in rows[:25]],
            "next_before_id": rows[24].id if len(rows) > 25 else None}


@router.get("/source-sessions/{session_id}", responses={200: {"model": RouteSourceResponse}})
def source_detail(session_id: int, version: int, session: Session = Depends(get_school_lunch_session)):
    row = session.get(RouteSourceSession, session_id)
    if row is None:
        raise HTTPException(404, "原本読取がありません")
    if row.version != version:
        raise HTTPException(409, "原本読取の版が変更されています。再読み込みしてください")
    return source_data(row)


@router.post("/source-sessions")
async def upload_source(diagram_id: int = Form(...), diagram_version: int = Form(...), page: int = Form(...), file: UploadFile = File(...), session: Session = Depends(get_school_lunch_session)):
    from src.services.route_source_service import sha
    diagram = session.query(RouteDiagram).filter_by(id=diagram_id).with_for_update().one_or_none()
    if not diagram:
        raise HTTPException(404, "対象日の動線下書きを先に作成してください")
    layout = session.get(KitchenLayout, diagram.layout_id)
    if diagram.version != diagram_version or layout.version != diagram.layout_version or service.versions(service.menus(session, diagram.facility_id, diagram.service_date)) != diagram.source_versions:
        raise HTTPException(409, "動線・献立・図面が変更されています。再読込してください")
    raw = await file.read(10*1024*1024+1)
    if len(raw) > 10*1024*1024:
        raise HTTPException(413, "原本PDFは10MB以下にしてください")
    try:
        image = await run_in_threadpool(render_pdf_page, raw, page)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = RouteSourceSession(diagram_id=diagram.id, diagram_version=diagram.version, layout_id=layout.id, layout_version=layout.version, source_versions=diagram.source_versions,
        filename=(file.filename or "source.pdf")[:300], source_pdf=raw, source_sha256=sha(raw), page=page, image=image, image_sha256=sha(image))
    session.add(row)
    session.flush()
    return source_data(row)


@router.get("/source-sessions/{session_id}/pdf")
def source_pdf(session_id: int, session: Session = Depends(get_school_lunch_session), expected_sources: str | None = None, expected_source_version: int | None = None):
    begin_export_read(session)
    row = session.query(RouteSourceSession).filter_by(id=session_id).populate_existing().one_or_none()
    if row is None:
        raise HTTPException(404, "原本読取がありません")
    if expected_sources is not None or expected_source_version is not None:
        if expected_sources is None or expected_source_version is None or expected_source_version <= 0:
            raise HTTPException(422, "原本・動線図の版を明示してください")
        if row.version != expected_source_version:
            raise HTTPException(409, "原本読取の版が変更されています。再読み込みしてください")
        diagram = session.query(RouteDiagram).filter_by(id=row.diagram_id).populate_existing().one_or_none()
        if diagram is None:
            raise HTTPException(409, "原本の動線図がありません")
        check_export_sources(session, [diagram], expected_sources)
        if row.layout_id != diagram.layout_id or row.layout_version != diagram.layout_version:
            raise HTTPException(409, "原本の図面版が一致しません")
    from src.services.route_source_service import source_content
    source_bytes, _ = source_content(row)
    return StreamingResponse(BytesIO(source_bytes), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="route-source.pdf"'})


@router.post("/source-sessions/{session_id}/extract", responses={200: {"model": RouteSourceResponse}})
def extract_source(session_id: int, body: SourceExtract, session: Session = Depends(get_school_lunch_session)):
    from src.services.route_source_service import current, detect
    import cv2
    row = source_row(session, session_id)
    current(session, row)
    if row.version != body.version or row.status == "confirmed":
        raise HTTPException(409, "確定済みまたは変更済みの読取です。新しく原本を登録してください")
    row.settings = {**body.model_dump(exclude={"version"}), "engine":"OpenCV HoughLinesP", "engine_version":cv2.__version__, "render_scale_to":2000}
    from src.services.route_source_service import source_content
    _, image = source_content(row)
    row.candidates = detect(image, row.settings)
    row.status = "extracted"
    row.version += 1
    session.flush()
    return source_data(row)


@router.post("/source-sessions/{session_id}/confirm", responses={200: {"model": RouteDiagramResponse}})
def confirm_source(session_id: int, body: SourceConfirm, session: Session = Depends(get_school_lunch_session)):
    from src.services.route_source_service import confirm
    return diagram_data(session, confirm(session, source_row(session, session_id), body))
