from pydantic import Field
from src.schemas.menu_process import ProcessDocument, Strict, Identifier

class SharedMenuWrite(Strict):
    name:str=Field(min_length=1,max_length=200); expected_version:int=Field(ge=0)
    status:str=Field(pattern='^(draft|confirmed)$'); source_month:str=Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    provenance:dict=Field(default_factory=dict); document:ProcessDocument; pattern_associations:list[dict]=Field(default_factory=list,max_length=100)
    change_note:str=Field(default='',max_length=2000)
class SharedMenuAdopt(Strict):
    shared_menu_revision_id:int=Field(ge=1); expected_menu_version:int=Field(ge=1); expected_binding_version:int=Field(ge=0); expected_recipe_revision:int=Field(ge=0)
    material_mapping:dict[Identifier,Identifier]=Field(min_length=1,max_length=150); change_note:str=Field(default='',max_length=2000)
