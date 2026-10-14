"""Documentation-only contracts; route dictionaries are never model-validated."""
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, WithJsonSchema


class FacilityVersion(BaseModel):
    model_config = ConfigDict(extra="allow")

    version_id: str
    valid_from: str | None
    valid_to: str | None
    facility: dict[str, Any]


class FacilityVersionWrite(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: FacilityVersion
    expected_revision: str


class FacilityVersionProjection(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["synced", "reconcile_required"]
    revision: str
    scope: Literal["unversioned_reference_only"]


class FacilityVersionsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    facility_id: str
    versions: list[FacilityVersion]
    reference: dict[str, Any]
    reference_status: Literal["unversioned_reference_only"]
    revision: str
    source: Literal["canonical_json"]


class FacilityVersionSaveResponse(FacilityVersionsResponse):
    version: FacilityVersion
    updated: Literal[True]
    projection: FacilityVersionProjection


class DatedFacilityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    facility_id: str
    effective_date: str
    version: FacilityVersion
    facility: dict[str, Any]
    config: dict[str, Any]
    revision: str
    source: Literal["canonical_json"]


def _inline_write_schema():
    # Inline generated definitions so FastAPI's dictionary schema has no orphan refs.
    schema = FacilityVersionWrite.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(deepcopy(definitions[value["$ref"].removeprefix("#/$defs/")]))
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(schema)


FacilityVersionBody = Annotated[dict, WithJsonSchema(_inline_write_schema())]
