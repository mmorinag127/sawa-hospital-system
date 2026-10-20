"""Strict request contracts for the school process-pattern pool."""
from pydantic import Field, model_validator

from src.schemas.menu_process import (Identifier, ProcessDocument, Strict, PatternExecutionChoice,
    PatternFixedEquipment, PatternMaterialSlot)


class PatternWrite(Strict):
    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)
    change_note: str = Field(default='', max_length=2000)
    material_slots: list[PatternMaterialSlot] = Field(min_length=1, max_length=150)
    document: dict
    fixed_equipment: list[PatternFixedEquipment] = Field(default_factory=list, max_length=300)
    execution_choices: list[PatternExecutionChoice] = Field(default_factory=list, max_length=300)


class PatternRevisionWrite(PatternWrite):
    pass


class PatternApply(Strict):
    application_id: Identifier
    pattern_revision_id: int = Field(ge=1)
    material_mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=150)
    execution_selections: list[PatternFixedEquipment] = Field(default_factory=list, max_length=300)


class ComposePatternsRequest(Strict):
    expected_menu_version: int = Field(ge=1)
    expected_binding_version: int = Field(ge=0)
    expected_recipe_revision: int = Field(ge=0)
    document: ProcessDocument
    applications: list[PatternApply] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def distinct_applications(self):
        if len({item.application_id for item in self.applications}) != len(self.applications):
            raise ValueError('採用IDが重複しています')
        return self
