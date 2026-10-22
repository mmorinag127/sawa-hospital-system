from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import json
import logging
from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import select
from src.models.menu_process_route import ProcessRouteArtifact
from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.api.school_scope import require_school_scope
from src.schemas.menu_process import SaveRequest, InferRequest, UseRecipeRequest, CorrectionRequest
from src.services import menu_process_service as service
from src.api import menu_process_contract as contract

router = APIRouter(prefix='/api/school-lunch', dependencies=[Depends(require_role('operator')), Depends(require_school_scope)])


def _preview_stream(events):
    # Run preflight before sending HTTP 200, retaining ordinary validation
    # errors. There is no detached job: disconnect closes this iterator.
    first = next(events)

    def lines():
        try:
            yield json.dumps(first, ensure_ascii=False) + '\n'
            for event in events:
                yield json.dumps(event, ensure_ascii=False) + '\n'
        except ValueError as exc:
            yield json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False) + '\n'
        except Exception:
            logging.getLogger(__name__).exception('Route preview streaming failed')
            yield json.dumps({'type': 'error', 'message': '動線生成に失敗しました。再度お試しください。'}, ensure_ascii=False) + '\n'
        finally:
            events.close()

    return StreamingResponse(lines(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

class RouteProfileDocument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    routing_policy:Literal['candidate_geometry']

class RouteProfileWrite(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:int|None=Field(default=None,ge=1); name:str=Field(min_length=1,max_length=200); template_id:Literal['kitchen-rice','kitchen-bread']; status:Literal['draft','confirmed']; expected_version:int=Field(ge=0); change_note:str=Field(default='',max_length=2000); document:RouteProfileDocument
class RouteGenerateWrite(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_menu_version:int=Field(ge=1); expected_process_revision:int=Field(ge=1); profile_revision_id:int=Field(ge=1)


class RoutePreviewWrite(InferRequest):
    """Current editor document, with no save or confirmation side effect."""


class DayRouteMenu(RoutePreviewWrite):
    menu_id: int = Field(ge=1)
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')


class DayRoutePreviewWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    service_date: date
    template_id: Literal['kitchen-rice', 'kitchen-bread']
    menus: list[DayRouteMenu] = Field(min_length=1, max_length=40)


@router.post('/process-route-preview')
def preview_day_routes(request: Request, body: DayRoutePreviewWrite, progress: bool = False, session=Depends(get_school_lunch_session)):
    from src.models.school_lunch import MenuExecution
    from src.services import menu_process_route_service as engine
    expected = set(session.scalars(select(MenuExecution.id).where(
        MenuExecution.facility_id == request.state.school_id,
        MenuExecution.service_date == body.service_date)).all())
    ids = [item.menu_id for item in body.menus]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise HTTPException(409, '選択学校・日付の全献立と入力が一致しません。読み込み直してください。')
    entries = []
    for item in body.menus:
        menu, binding, recipe, _ = service.state(session, item.menu_id)
        service.check_versions(menu, binding, recipe, item)
        document = item.document.model_dump()
        service.check_materials(menu, document)
        entries.append((menu, document, item.color))
    try:
        if progress:
            return _preview_stream(engine.preview_day_events(entries, body.template_id))
        return engine.preview_day(entries, body.template_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/menu-executions/{menu_id}/process', responses={200: {'model': contract.ProcessContext}})
def read_process(menu_id: int, session=Depends(get_school_lunch_session)):
    return service.context(session, menu_id)


@router.get('/menu-executions/{menu_id}/process/route-options', responses={200: {'model': contract.RouteOptions}})
def process_route_options(menu_id: int, session=Depends(get_school_lunch_session)):
    # Confirm menu existence before exposing a route configuration surface.
    service.menu_for(session, menu_id)
    from src.services import menu_process_route_service as route_service
    return route_service.options()

@router.get('/menu-executions/{menu_id}/process/question-drawing', responses={200: {'model': contract.ProcessQuestionDrawing}})
def question_drawing(menu_id: int, template_id: Literal['kitchen-rice', 'kitchen-bread'], session=Depends(get_school_lunch_session)):
    service.menu_for(session, menu_id)
    from src.services.process_question_drawing import drawing
    try:
        return drawing(template_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/menu-executions/{menu_id}/process/route-profiles', responses={200: {'model': list[contract.RouteProfile]}})
def list_route_profiles(menu_id:int, session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_persistence_service as profiles
    return profiles.profiles(session,service.menu_for(session,menu_id).facility_id)

@router.post('/menu-executions/{menu_id}/process/route-profiles', responses={200: {'model': contract.RouteProfile}})
def save_route_profile(menu_id:int, body:RouteProfileWrite, session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_persistence_service as profiles
    return profiles.save_profile(session,service.menu_for(session,menu_id).facility_id,body.model_dump())

@router.get('/menu-executions/{menu_id}/process/routes', responses={200: {'model': list[contract.RouteArtifact]}})
def list_process_routes(menu_id:int, session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_persistence_service as profiles
    service.menu_for(session,menu_id)
    return [profiles.artifact_data(x) for x in session.scalars(select(ProcessRouteArtifact).where(ProcessRouteArtifact.menu_execution_id==menu_id).order_by(ProcessRouteArtifact.id.desc())).all()]

@router.get('/menu-executions/{menu_id}/process/routes/{artifact_id}', responses={200: {'model': contract.RouteArtifact}})
def get_process_route(menu_id:int,artifact_id:int,session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_persistence_service as profiles
    a=session.get(ProcessRouteArtifact,artifact_id)
    if not a or a.menu_execution_id!=menu_id: raise HTTPException(404,'動線成果物が見つかりません')
    return profiles.artifact_data(a)


@router.put('/menu-executions/{menu_id}/process', responses={200: {'model': contract.ProcessContext}})
def save_process(menu_id: int, payload: SaveRequest, session=Depends(get_school_lunch_session),
                 user=Depends(require_role('operator'))):
    return service.save(session, menu_id, payload, user.role)


@router.post('/menu-executions/{menu_id}/process/infer', responses={200: {'model': contract.ProcessProposal}})
def infer_process(menu_id: int, payload: InferRequest, supply_only: bool=False, drawing_only: bool=False, session=Depends(get_school_lunch_session)):
    if supply_only and drawing_only:
        raise HTTPException(422, '推論対象を一つだけ選択してください')
    return service.infer(session, menu_id, payload, supply_only=supply_only, drawing_only=drawing_only)


@router.post('/menu-executions/{menu_id}/process/corrections', responses={200: {'model': contract.ProcessCorrectionProposal}})
def correct_process(menu_id: int, payload: CorrectionRequest, session=Depends(get_school_lunch_session)):
    return service.correct(session, menu_id, payload)


@router.post('/menu-executions/{menu_id}/process/prepare-defaults', responses={200: {'model': contract.ProcessDefaults}})
def prepare_process_defaults(menu_id: int, payload: InferRequest, session=Depends(get_school_lunch_session)):
    from src.services.process_route_defaults import prepare
    try:
        return prepare(session, menu_id, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/menu-executions/{menu_id}/process/use-recipe', responses={200: {'model': contract.ProcessContext}})
def reuse_process(menu_id: int, payload: UseRecipeRequest, session=Depends(get_school_lunch_session),
                 user=Depends(require_role('operator'))):
    # Required displayed recipe revision is fenced in this same owned Session
    # before the service's existing binding/menu CAS and business writes.
    return service.use_recipe(session, menu_id, payload, user.role)


@router.post('/menu-executions/{menu_id}/process/use-recipe/compatibility', responses={200: {'model': contract.ReuseCompatibility}})
def reuse_process_compatibility(menu_id: int, payload: UseRecipeRequest, session=Depends(get_school_lunch_session)):
    return service.reuse_compatibility_preview(session, menu_id, payload)


@router.get('/menu-executions/{menu_id}/process/route-input', responses={200: {'model': contract.RouteInput}})
def read_route_input(menu_id: int, session=Depends(get_school_lunch_session)):
    return service.route_input(session, menu_id)


@router.post('/menu-executions/{menu_id}/process/route', responses={200: {'model': contract.RouteArtifact}})
def generate_process_route(menu_id: int, body:RouteGenerateWrite, session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_persistence_service as profiles
    menu, _, _, revision = service.state(session, menu_id)
    if not revision or revision.status != 'confirmed':
        raise HTTPException(409, '動線生成には確認済みの工程が必要です')
    if revision.source_snapshot != service.snapshot(menu):
        raise HTTPException(409, '献立が工程確認後に変更されています。再確認してください。')
    if menu.version!=body.expected_menu_version or revision.id!=body.expected_process_revision: raise HTTPException(409,'献立または工程が更新されました')
    try:return profiles.generate(session,menu,revision,body.profile_revision_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@router.post('/menu-executions/{menu_id}/process/route-preview')
def preview_process_route(menu_id: int, body: RoutePreviewWrite, progress: bool = False, session=Depends(get_school_lunch_session)):
    from src.services import menu_process_route_service as engine
    menu, binding, recipe, _ = service.state(session, menu_id)
    service.check_versions(menu, binding, recipe, body)
    document = body.document.model_dump()
    service.check_materials(menu, document)
    try:
        if progress:
            return _preview_stream(engine.preview_events(document, menu))
        return engine.preview(document, menu)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
