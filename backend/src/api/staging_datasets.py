"""Per-browser staging selection. Production is never a selectable target."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.auth import require_role
from src import db, staging_data as datasets

router = APIRouter(dependencies=[Depends(require_role("operator"))])


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str


@router.get("/system/staging-dataset-ready")
def ready(response: Response):
    if not datasets.staging_runtime():
        raise HTTPException(404, "Not found")
    with db.get_engine().connect() as connection:
        from sqlalchemy import text
        connection.execute(text("SELECT 1"))
    if datasets.current_dataset() != "test":
        from src.services.canonical_facility_store import read_snapshot
        read_snapshot()
    response.headers["Cache-Control"] = "no-store"
    return {"dataset": datasets.current_dataset(), "ready": True}


@router.get("/staging-datasets")
def choices(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not datasets.staging_runtime():
        return {"enabled": False}
    selected = request.cookies.get(datasets.COOKIE, "test")
    options = [{"id": "test", "label": "テスト用", "available": True}]
    try:
        snapshot = datasets.manifest()
        options.append({"id": snapshot["id"], "label": "本番再現用", "available": True,
                        "snapshot_at": snapshot["snapshot_at"]})
        error = None if selected in {item["id"] for item in options} else "選択したコピーは利用できません。テスト用を選び直してください。"
    except datasets.DatasetUnavailable:
        options.append({"id": "unavailable", "label": "本番再現用（準備未完了）", "available": False})
        error = None if selected == "test" else "本番コピーを確認できません。テスト用へ戻すか管理者に確認してください。"
    return {"enabled": True, "selected": selected, "options": options, "error": error}


@router.put("/staging-datasets")
def select_dataset(body: Selection, response: Response):
    if not datasets.staging_runtime():
        raise HTTPException(404, "Not found")
    try:
        with datasets.dataset_context(body.dataset):
            with db.get_engine().connect() as connection:
                from sqlalchemy import text
                connection.execute(text("SELECT 1"))
    except datasets.DatasetUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    response.set_cookie(datasets.COOKIE, body.dataset, path="/", secure=True,
                        httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"selected": body.dataset}
