from pydantic import BaseModel


class JobFailure(BaseModel):
    code: str | None
    message: str | None


class JobStatusRecord(BaseModel):
    domain: str
    jobId: str | int
    inputVersion: str | int | None
    inputReference: str | None
    attemptIdentity: str | int | None
    state: str | None
    sourceState: str | None
    sourceLabel: str
    failure: JobFailure | None
    artifactReference: str | int | None
    deliveryGeneration: int | None
