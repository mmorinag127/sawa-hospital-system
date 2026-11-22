"""Reopen saved FAX reviews without recovering a provider session."""
import json

from .execution_binding import approve_native_upgrade, native_upgrade, record
from .sandbox import run_blocking
from .store import Conflict, now


def _check_saved_reviews(run, db, expected=None):
    ident = run["id"]
    if db.execute("SELECT 1 FROM commands WHERE run_id=? AND status!='done' LIMIT 1", (ident,)).fetchone():
        raise Conflict("結果未確定の命令があります。再送せず確認を止めます")
    rows = [tuple(row[key] for key in ("fax_id", "state", "snapshot", "review", "decision"))
            for row in db.execute("SELECT fax_id,state,snapshot,review,decision FROM fax_items "
                                  "WHERE run_id=? ORDER BY fax_id", (ident,))]
    if expected is not None:
        if rows != expected:
            raise Conflict("照合中にFAX確認案または回答が変更されました")
        return rows
    if (not rows or not any(state in ("awaiting_approval", "needs_correction") for _, state, *_ in rows)
            or any(state not in ("awaiting_approval", "needs_correction", "approved", "completed")
                   or not review for _, state, _, review, _ in rows)):
        raise Conflict("全対象の確認案が保存済みの依頼だけを開けます")
    return rows


async def reopen(business, run, system):
    from .business_contracts import owned
    from .fax_preview import review_evidence_id

    if (run.get("status") != "reconciliation_required" or not owned(run)
            or run.get("task_id") != "hospital_pending_faxes" or run["domains"] != ["hospital"]):
        raise Conflict("結果照合待ちの未処理FAX依頼だけを開けます")
    ident, store, tools = run["id"], business.store, business.tools
    plan = await run_blocking(native_upgrade, store, tools, run, system)
    if not plan and not await run_blocking(record, store, tools, run, system):
        raise Conflict("開始時の業務契約を確認できません")

    def snapshot():
        with store.read() as db:
            return _check_saved_reviews(run, db)

    saved = await run_blocking(snapshot)
    await tools.sandbox.start(run)
    await run_blocking(business.require_copy, run)
    for fax_id, state, serialized, review, _ in saved:
        if state == "completed":
            continue
        source, review = json.loads(serialized), json.loads(review)
        evidence_id = review_evidence_id(review)
        order_id = source.get("order_id")
        if any(not isinstance(x, str) or not x.isascii() or not x.isalnum()
               for x in (fax_id, order_id, evidence_id)):
            raise Conflict("確認案の注文・採用OCRを特定できません")
        live = (await tools.faxes.get(run, f"/ingest/uploads/{fax_id}")).json()
        workflow = (await tools.faxes.get(run, f"/orders/{order_id}/workflow-v2")).json()
        if (not source.get("sha256") or source["sha256"] != review["original"].get("sha256")
                or review.get("order_id") != order_id
                or any(not source.get(key) or source[key] != live.get(actual) for key, actual in (
                    ("sha256", "content_sha256"), ("order_id", "current_order_id"),
                    ("document_id", "current_document_id"), ("facility_id", "facility_hint"), ("week", "week_hint")))
                or workflow.get("selected_ocr_result_id") != evidence_id
                or workflow.get("revision") != review.get("revision")):
            raise Conflict("確認案と現在の原本・採用OCR・版が一致しません")

    def publish(db):
        _check_saved_reviews(run, db, expected=saved)
        current = db.execute("SELECT task_inputs FROM runs WHERE id=?", (ident,)).fetchone()
        if not current or json.loads(current["task_inputs"]) != run["task_inputs"]:
            raise Conflict("照合中に依頼が変更されました")
        context = json.loads(current["task_inputs"])
        context["business_workflow"].update(phase="approval", fax_review_only=True, attempts=0, next_attempt=0)
        update = db.execute("UPDATE runs SET status='running',task_inputs=?,updated=? "
                            "WHERE id=? AND generation=? AND status='reconciliation_required' AND task_inputs=?",
                            (json.dumps(context, ensure_ascii=False), now(), ident, run["generation"], current["task_inputs"]))
        if update.rowcount != 1:
            raise Conflict("照合中に依頼が停止または変更されました")
        db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                   (ident, "fax_review_reopened", json.dumps({"fax_count": len(saved),
                    "generation": run["generation"], "provider_called": False}), now()))

    def finish():
        if plan:
            approve_native_upgrade(store, tools, run, system, generation=run["generation"],
                                   previous_sha256=plan["previous_sha256"], current_sha256=plan["current_sha256"],
                                   publish=publish)
        else:
            record(store, tools, run, system)
            with store.tx() as db:
                publish(db)

    await run_blocking(finish)
