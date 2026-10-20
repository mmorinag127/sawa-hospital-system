from fastapi import APIRouter, Depends, HTTPException, Request
from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.api.school_scope import require_school_scope
from src.schemas.process_patterns import ComposePatternsRequest, PatternWrite
from src.services import menu_process_service as processes
from src.services import process_pattern_service as patterns

router = APIRouter(prefix='/api/school-lunch', dependencies=[Depends(require_role('operator')), Depends(require_school_scope)])

def _owned_menu(request, session, menu_id):
    menu = processes.menu_for(session, menu_id)
    if menu.facility_id != request.state.school_id:
        raise HTTPException(404, '選択学校の献立が見つかりません')
    return menu

@router.get('/menu-executions/{menu_id}/process-patterns')
def list_process_patterns(request: Request, menu_id: int, session=Depends(get_school_lunch_session)):
    return patterns.list_patterns(session, _owned_menu(request, session, menu_id).facility_id)

@router.post('/menu-executions/{menu_id}/process-patterns')
def create_process_pattern(request: Request, menu_id: int, body: PatternWrite, session=Depends(get_school_lunch_session)):
    menu = _owned_menu(request, session, menu_id)
    if body.expected_version != 0:
        raise HTTPException(422, '新規パターンのexpected_versionは0です')
    return patterns.create_pattern(session, menu.facility_id, body)

@router.post('/menu-executions/{menu_id}/process-patterns/compose')
def compose_process_patterns(request: Request, menu_id: int, body: ComposePatternsRequest, session=Depends(get_school_lunch_session)):
    _owned_menu(request, session, menu_id)
    return patterns.compose(session, menu_id, body)

@router.get('/menu-executions/{menu_id}/process-patterns/{pattern_id}')
def get_process_pattern(request: Request, menu_id: int, pattern_id: int, session=Depends(get_school_lunch_session)):
    return patterns.get_pattern(session, _owned_menu(request, session, menu_id).facility_id, pattern_id)

@router.post('/menu-executions/{menu_id}/process-patterns/{pattern_id}/revisions')
def revise_process_pattern(request: Request, menu_id: int, pattern_id: int, body: PatternWrite, session=Depends(get_school_lunch_session)):
    return patterns.revise_pattern(session, _owned_menu(request, session, menu_id).facility_id, pattern_id, body)
