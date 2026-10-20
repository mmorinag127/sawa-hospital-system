"""Versioned operator decisions; raw OCR is never modified here."""
from datetime import date, datetime
import hashlib
import json

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from src.models.school_lunch_source_review import SchoolLunchSourceReview


class ReviewConflict(ValueError):
    pass


FIELDS = ("source", "source_page", "raw_day", "facility_name", "service_date", "menu_name", "target_food", "instruction", "container")


def source_snapshot(job):
    if job.get("status") != "succeeded":
        raise ValueError("OCRが正常完了した資料のみ確認できます。再読取中・失敗中の結果は使用できません。")
    result = job.get("result") or {}
    if result.get("source_kind") != "allergy_instructions":
        raise ValueError("アレルギー対応資料のみ確認結果を保存できます。")
    originals = result.get("allergy_instructions") or []
    keys = [row.get("source") for row in originals]
    if not keys or any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("原本行を一意に特定できません。")
    evidence = {"stored_pdf_ref": job.get("stored_pdf_ref"), "result": result}
    fingerprint = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return originals, fingerprint


def get_review(session, job_id, job):
    originals, fingerprint = source_snapshot(job)
    saved = session.get(SchoolLunchSourceReview, job_id)
    stale = saved is not None and saved.source_fingerprint != fingerprint
    rows = saved.rows if saved is not None and not stale else [
        {**{key: row.get(key, "") for key in FIELDS}, "confirmed": False}
        for row in originals
    ]
    return {"version": saved.version if saved else 0, "source_fingerprint": fingerprint,
            "stale": stale, "rows": rows, "originals": originals,
            "updated_at": saved.updated_at.isoformat() if saved else None}


def save_review(session, job_id, job, payload):
    originals, fingerprint = source_snapshot(job)
    if payload["source_fingerprint"] != fingerprint:
        raise ReviewConflict("原本が更新されています。再読込して確認してください。")
    rows = payload["rows"]
    by_source = {row["source"]: row for row in originals}
    keys = [row["source"] for row in rows]
    if len(keys) != len(set(keys)) or set(keys) != set(by_source):
        raise ValueError("原本行の追加・削除・重複はできません。")
    for row in rows:
        original = by_source[row["source"]]
        if any(row[key] != original.get(key, "") for key in ("source_page", "raw_day")):
            raise ValueError("原本ページ・原文の日付は変更できません。")
        if row["confirmed"]:
            if any(not row[key].strip() for key in ("facility_name", "service_date", "menu_name", "target_food", "instruction")):
                raise ValueError("確認済みの行には学校・日付・献立・対象食材・対応内容が必要です。")
            try:
                parsed = date.fromisoformat(row["service_date"])
                if parsed.isoformat() != row["service_date"]:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError("確認済みの日付は YYYY-MM-DD で指定してください。") from exc
    values = dict(version=payload["version"] + 1, source_fingerprint=fingerprint,
                  rows=rows, updated_at=datetime.utcnow())
    if payload["version"] == 0:
        try:
            with session.begin_nested():
                session.add(SchoolLunchSourceReview(job_id=job_id, **values))
                session.flush()
        except IntegrityError as exc:
            raise ReviewConflict("別の操作で保存されています。再読込してください。") from exc
    else:
        result = session.execute(update(SchoolLunchSourceReview).where(
            SchoolLunchSourceReview.job_id == job_id,
            SchoolLunchSourceReview.version == payload["version"],
        ).values(**values))
        if result.rowcount != 1:
            raise ReviewConflict("別の操作で保存されています。再読込してください。")
    session.flush()
    session.expire_all()
    return get_review(session, job_id, job)
