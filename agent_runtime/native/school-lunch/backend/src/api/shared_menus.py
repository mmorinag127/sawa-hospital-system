from fastapi import APIRouter,Depends,Request,Query
from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.api.school_scope import require_school_scope
from src.schemas.shared_menus import SharedMenuWrite,SharedMenuAdopt
from src.services import shared_menu_service as service
from src.api import menu_process_contract as process_contract
router=APIRouter(prefix='/api/school-lunch',dependencies=[Depends(require_role('operator'))])
@router.post('/shared-menu-review-seeds/july-2026')
def import_july_review(session=Depends(get_school_lunch_session)):
    from src.services.shared_menu_review import import_july
    return import_july(session)

@router.get('/shared-menu-editor-options')
def shared_menu_editor_options(session=Depends(get_school_lunch_session)):
    return service.editor_options(session)

@router.get('/shared-menus')
def list_shared_menus(q:str|None=None,source_month:str|None=Query(default=None,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),summary:bool=True,
                      limit:int=Query(default=100,ge=1,le=100),after_name:str|None=None,after_id:int|None=Query(default=None,ge=1),
                      status:str|None=Query(default=None,pattern='^(draft|confirmed)$'),session=Depends(get_school_lunch_session)):
    return service.list_(session,q,source_month,summary=summary,limit=limit,after_name=after_name,after_id=after_id,status=status)
@router.post('/shared-menus')
def create_shared_menu(body:SharedMenuWrite,session=Depends(get_school_lunch_session)): return service.create(session,body)
@router.get('/shared-menus/{shared_menu_id}')
def get_shared_menu(shared_menu_id:int,session=Depends(get_school_lunch_session)): return service.get(session,shared_menu_id)
@router.get('/shared-menus/{shared_menu_id}/revisions')
def list_shared_menu_revisions(shared_menu_id:int,before_number:int|None=Query(default=None,ge=1),limit:int=Query(default=25,ge=1,le=100),session=Depends(get_school_lunch_session)):
    return service.revision_page(session,shared_menu_id,before_number=before_number,limit=limit)
@router.get('/shared-menus/{shared_menu_id}/revisions/{revision_id}')
def get_shared_menu_revision(shared_menu_id:int,revision_id:int,session=Depends(get_school_lunch_session)):
    return service.get_revision(session,shared_menu_id,revision_id)
@router.post('/shared-menus/{shared_menu_id}/revisions')
def revise_shared_menu(shared_menu_id:int,body:SharedMenuWrite,session=Depends(get_school_lunch_session)): return service.revise(session,shared_menu_id,body)
@router.post('/menu-executions/{menu_id}/shared-menu-adoptions', dependencies=[Depends(require_school_scope)],
             responses={200: {'model': process_contract.SharedMenuAdoptionContext}})
def adopt_shared_menu(request:Request,menu_id:int,body:SharedMenuAdopt,session=Depends(get_school_lunch_session)):
    from src.services.menu_process_service import menu_for
    if menu_for(session,menu_id).facility_id!=request.state.school_id: from fastapi import HTTPException; raise HTTPException(404,'選択学校の献立が見つかりません')
    return service.adopt(session,menu_id,body)
