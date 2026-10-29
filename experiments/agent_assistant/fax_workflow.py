"""Per-FAX review queue. Human approval is bound to captured native business state."""
import hashlib
import json
from uuid import uuid4

from .store import Conflict
from .sandbox import run_blocking


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class FaxWorkflow:
    PAGE_SIZE = 5

    def __init__(self, tools):
        self.tools, self.store = tools, tools.store

    @staticmethod
    def active(run):
        return run.get("task_id") in ("hospital_pending_faxes", "hospital_review_faxes")

    async def get(self, run, path):
        response = await self.tools.sandbox.request(run, "hospital", "GET", path,
            authorization=self.tools.authorizations.get(run["id"]))
        if not response.is_success:
            raise Conflict(f"FAXの最新状態を取得できません（HTTP {response.status_code}）")
        return response

    @staticmethod
    def _decode_item(row):
        item = dict(row)
        item.pop('rowid', None)
        item['snapshot'] = json.loads(item['snapshot'])
        for key in ('review', 'decision'):
            item[key] = json.loads(item[key]) if item[key] else None
        return item

    def items(self, run_id):
        with self.store.read() as db:
            rows = db.execute("SELECT * FROM fax_items WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return [self._decode_item(row) for row in rows]

    def page(self, run_id, *, after=0, state=''):
        if type(after) is not int or after < 0 or state not in ('', 'awaiting_approval'):
            raise ValueError('FAXの取得範囲が不正です')
        with self.store.read() as db:
            states = {row[0]: row[1] for row in db.execute(
                'SELECT state,COUNT(*) FROM fax_items WHERE run_id=? GROUP BY state', (run_id,))}
            condition = ' AND state=?' if state else ''
            parameters = (run_id, after, state) if state else (run_id, after)
            rows = db.execute('SELECT rowid,* FROM fax_items WHERE run_id=? AND rowid>?'
                + condition + ' ORDER BY rowid LIMIT ?', (*parameters, self.PAGE_SIZE + 1)).fetchall()
        items = [self._decode_item(row) for row in rows[:self.PAGE_SIZE]]
        return {'items': items, 'summary': {'total': sum(states.values()), 'states': states},
                'next_after': rows[self.PAGE_SIZE - 1]['rowid'] if len(rows) > self.PAGE_SIZE else None, 'state': state}

    def item(self, run_id, fax_id):
        with self.store.read() as db:
            row = db.execute('SELECT * FROM fax_items WHERE run_id=? AND fax_id=?', (run_id, fax_id)).fetchone()
        if row is None:
            return None
        return self._decode_item(row)

    def for_order(self, run_id, order_id):
        if not isinstance(order_id, str) or not order_id:
            raise Conflict('注文の識別情報がありません')
        with self.store.read() as db:
            row = db.execute('SELECT * FROM fax_items WHERE run_id=? AND '
                + self.store.fax_order_expression + '=? ORDER BY rowid LIMIT 1', (run_id, order_id)).fetchone()
        return self._decode_item(row) if row else None

    def labels(self, run_id, events):
        identities = sorted({str(event['payload']['fax_id']) for event in events
            if event['kind'] in ('fax_review', 'fax_decision') and 'fax_id' in event['payload']})
        if not identities:
            return {}
        with self.store.read() as db:
            return {str(row['fax_id']): json.loads(row['snapshot']).get('name') or '対象のFAX'
                for row in db.execute('SELECT fax_id,snapshot FROM fax_items WHERE run_id=? AND fax_id IN ('
                    + ','.join('?' for _ in identities) + ')', (run_id, *identities))}

    def set(self, run_id, fax_id, **fields):
        with self.store.tx() as db:
            db.execute("UPDATE fax_items SET " + ",".join(k+"=?" for k in fields) + " WHERE run_id=? AND fax_id=?",
                (*[json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for v in fields.values()], run_id, fax_id))

    def set_observed(self, run_id, item, **fields):
        """Background refresh may never overwrite a newer review or human answer."""
        encode = lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        with self.store.tx() as db:
            db.execute('UPDATE fax_items SET '+','.join(key+'=?' for key in fields)
                +' WHERE run_id=? AND fax_id=? AND state=? AND review IS ? AND decision IS ?',
                (*[encode(value) for value in fields.values()], run_id, item['fax_id'], item['state'],
                 encode(item['review']), encode(item['decision'])))

    async def initialize(self, run):
        def batch_exists():
            with self.store.read() as db:
                return db.execute("SELECT 1 FROM fax_batches WHERE run_id=?", (run["id"],)).fetchone() is not None
        exists = await run_blocking(batch_exists)
        if exists:
            return
        if run.get("task_id")=="hospital_review_faxes":
            selected=run["task_inputs"]["targets"]
            data={"items":[x["data"] for x in selected],"total":len(selected)}
        else:
            data = (await self.get(run, "/ingest/pending-faxes")).json()
        if not isinstance(data.get("items"), list) or data.get("total") != len(data["items"]):
            raise Conflict("未処理FAXの全件を取得できません")
        def save_batch():
            with self.store.tx() as db:
                if db.execute("SELECT 1 FROM fax_batches WHERE run_id=?", (run["id"],)).fetchone():
                    return False
                for item in data["items"]:
                    db.execute("INSERT INTO fax_items(run_id,fax_id,snapshot,state) VALUES(?,?,?,'processing')",
                        (run["id"], str(item["id"]), json.dumps(item, ensure_ascii=False)))
                db.execute("INSERT INTO fax_batches VALUES(?)", (run["id"],))
            return True
        if not await run_blocking(save_batch):
            return
        await run_blocking(self.store.event, run["id"], "fax_batch", {"total": data["total"]})

    async def current_source(self, run, item):
        source = (await self.get(run, f"/ingest/uploads/{item['fax_id']}")).json()
        snap = item["snapshot"]
        for original, actual in (("sha256", "content_sha256"), ("facility_id", "facility_hint"), ("week", "week_hint")):
            if not snap.get(original) or snap[original] != source.get(actual):
                raise Conflict("FAXの原本・施設・対象週が依頼開始時から変更されています")
        if source.get("skip_ocr"):
            raise Conflict("OCRを省略したFAXはこの業務で処理できません")
        for key, actual in (("order_id", "current_order_id"), ("document_id", "current_document_id")):
            if snap.get(key) and snap[key] != source.get(actual):
                raise Conflict("FAXと注文・原本の対応が変更されています")
            if source.get(actual):
                snap[key] = source[actual]
        await run_blocking(self.set, run["id"], item["fax_id"], snapshot=snap)
        return source

    def accepted_recovery(self, run, item):
        if run.get("task_id") != "hospital_review_faxes": return False
        with self.store.read() as db:
            commands=db.execute("SELECT request,result FROM commands WHERE run_id=? AND status='done'",(run["id"],)).fetchall()
        for row in commands:
            request=json.loads(row["request"]); result=json.loads(row["result"])
            if request["name"]=="hospital_accept_recognition" and result.get("ok") and str(request["arguments"].get("parameters",{}).get("ids",{}).get("order_id"))==str(item["snapshot"].get("order_id")):
                return True
        return False

    async def capture(self, run, item, proposed_sheet=None):
        source = await self.current_source(run, item)
        order_id = source.get("current_order_id")
        if (source.get("status") != "completed" and not await run_blocking(self.accepted_recovery, run,item)) or not order_id or not source.get("current_document_id"):
            raise Conflict("このFAXのOCRは完了していません")
        workflow = (await self.get(run, f"/orders/{order_id}/workflow-v2")).json()
        if not workflow.get("selected_ocr_result_id"):
            raise Conflict("採用OCRが必要です")
        if workflow.get("saved_sheet_id"):
            sheet = (await self.get(run, f"/orders/{order_id}/workflow-v2/sheet")).json()
            if workflow["saved_sheet_id"] != sheet.get("saved_sheet_id") or sheet.get("source_ocr_result_id") != workflow["selected_ocr_result_id"]:
                raise Conflict("採用OCR・保存シートの系譜が一致しません")
        else:
            # Explicit pre-save stage, using the native selected-evidence source.
            # It never replaces an existing saved operator draft.
            sheet = (await self.get(run, f"/orders/{order_id}/workflow-v2/sheet-source")).json()
            if sheet.get("selected_ocr_result_id") != workflow["selected_ocr_result_id"]:
                raise Conflict("採用OCRとシート候補の系譜が一致しません")
        if not isinstance(sheet.get("sheet"), dict):
            raise Conflict("確認する数量シートがありません")
        if proposed_sheet is not None:
            if not isinstance(proposed_sheet, dict) or not isinstance(proposed_sheet.get("rows"), list):
                raise ValueError("確認用シートの行が必要です")
            canonical = sheet["sheet"]
            if {k: v for k, v in proposed_sheet.items() if k != "rows"} != {k: v for k, v in canonical.items() if k != "rows"}:
                raise Conflict("確認案で施設・期間・列・セル対応などの正本情報を変更できません")
            sheet = {**sheet, "sheet": proposed_sheet}
        # Native revision guards the human click. This digest also survives later
        # legitimate review/bagging transitions without allowing quantity changes.
        business = {"source": {k: source.get(k) for k in ("id", "content_sha256", "facility_hint", "week_hint", "current_order_id", "current_document_id")},
            "workflow": {k: workflow.get(k) for k in ("selected_ocr_result_id", "facility_id", "week_start", "week_end", "template_id", "template_version_id", "template_source", "quad_override", "row_axis_override", "recognition_override_invalidation", "expanded_cell_copy_mode")},
            "sheet": sheet["sheet"]}
        return {"signature": digest(business), "revision": workflow["revision"], "sheet": sheet["sheet"], "workflow": workflow}

    async def refresh(self, run):
        if not self.active(run):
            raise ValueError("未処理FAXの定型業務でのみ使用できます")
        await self.initialize(run)
        for item in await run_blocking(self.items, run["id"]):
            try:
                source = await self.current_source(run, item)
                status = source.get("status")
                state = item["state"]
                if status in ("pending", "processing"):
                    state = status
                elif status != "completed" and not await run_blocking(self.accepted_recovery, run,item):
                    raise Conflict("FAX取込が要確認または失敗です。履歴を確認してください")
                elif source.get("current_order_id"):
                    wf = (await self.get(run, f"/orders/{source['current_order_id']}/workflow-v2")).json()
                    if wf.get("state") == "confirmed":
                        state = "completed"
                    elif state not in ("awaiting_approval", "approved", "needs_correction"):
                        state = "ready"
                else:
                    raise Conflict("OCR完了FAXに対応する注文がありません")
                await run_blocking(self.set_observed, run["id"], item, state=state)
            except Conflict as exc:
                await run_blocking(self.set_observed, run["id"], item, state="blocked", decision={"message": str(exc)})
        return {"ok": True, "items": await run_blocking(self.items, run["id"])}

    async def submit(self, run, fax_id, summary, sheet=None):
        item = await run_blocking(self.item, run["id"], fax_id)
        if not item or not summary.strip() or len(summary) > 4000:
            raise ValueError("対象FAXと照合結果が必要です")
        captured = await self.capture(run, item, proposed_sheet=sheet)
        basis = await self.capture(run, item)
        if basis["revision"] != captured["revision"]:
            raise Conflict("確認資料の取得中に版が変わりました")
        # An unchanged repeated request keeps the exact human review identity.
        old = item["review"]
        if old and old["signature"] == captured["signature"] and old["revision"] == captured["revision"] and item["state"] in ("awaiting_approval", "approved"):
            return {"ok": True, "review": old, "state": item["state"]}
        order_id = item["snapshot"]["order_id"]
        original = await self.get(run, f"/orders/{order_id}/document")
        if hashlib.sha256(original.content).hexdigest() != item["snapshot"]["sha256"]:
            raise Conflict("取得した原本PDFが依頼対象のハッシュと一致しません")
        artifact = self.tools.artifact(run["id"], original.content, f"FAX-{fax_id}.pdf")
        review = {"id": uuid4().hex, "signature": captured["signature"], "native_signature": basis["signature"], "revision": captured["revision"],
            "summary": summary, "sheet": captured["sheet"], "original": artifact, "order_id": order_id}
        await run_blocking(self.set, run["id"], fax_id, state="awaiting_approval", review=review, decision=None)
        await run_blocking(self.store.event, run["id"], "fax_review", {"fax_id": fax_id, "review_id": review["id"], "summary":summary})
        return {"ok": True, "queued_for_human": True, "review": review}

    async def decide(self, run, fax_id, review_id, approve, message):
        if run["status"] != "running" or not self.active(run):
            raise Conflict("この依頼は回答を受け付けていません")
        item = await run_blocking(self.item, run["id"], fax_id)
        if not item or not item["review"] or item["review"]["id"] != review_id:
            raise Conflict("確認画面が古くなっています。最新の内容を開いてください")
        decision = {"review_id": review_id, "approve": approve, "message": message}
        if item["decision"] and all(item["decision"].get(k) == v for k, v in decision.items()):
            return {"ok": True, "replayed": True}
        if item["state"] != "awaiting_approval":
            raise Conflict("この確認には既に回答済みです")
        current = await self.capture(run, item, proposed_sheet=item["review"]["sheet"])
        basis = await self.capture(run, item)
        if (await run_blocking(self.store.get, run["id"]))["generation"] != run["generation"] or (await run_blocking(self.store.get, run["id"]))["status"] != "running":
            raise Conflict("依頼は停止されました")
        if current["signature"] != item["review"]["signature"] or current["revision"] != item["review"]["revision"] or basis["signature"] != item["review"]["native_signature"] or basis["revision"] != current["revision"]:
            await run_blocking(self.set, run["id"], fax_id, state="ready", review=None, decision=None)
            raise Conflict("確認後に内容・版が変わりました。再確認が必要です")
        if not approve and not message.strip():
            raise ValueError("訂正してほしい箇所を入力してください")
        decision["approved_revision"] = current["revision"]
        decision["native_signature"] = basis["signature"]
        await run_blocking(self.set, run["id"], fax_id, state="approved" if approve else "needs_correction", decision=decision)
        await run_blocking(self.store.event, run["id"], "fax_decision", {"fax_id": fax_id, **decision})
        return {"ok": True}

    async def gate(self, run, name, args):
        if not self.active(run):
            return
        await self.initialize(run)
        items = await run_blocking(self.items, run["id"])
        from .catalog import OPERATIONS
        op = OPERATIONS[name]
        if op.method == "GET":
            if name.startswith("hospital_export_") and str(args.query.get("order_id")) not in {str(x["snapshot"].get("order_id")) for x in items if x["snapshot"].get("order_id")} :
                raise Conflict("帳票は対象FAXの注文IDを一件ずつ指定してください")
            return
        if op.domain != "hospital" or name == "hospital_upload_fax":
            raise Conflict("選択した定型業務の対象外です")
        fax_id, order_id = args.ids.get("uploaded_pdf_id"), args.ids.get("order_id")
        item = next((x for x in items if (fax_id is not None and str(fax_id) == x["fax_id"]) or
                     (order_id is not None and str(order_id) == str(x["snapshot"].get("order_id")))), None)
        if not item:
            raise Conflict("依頼開始時に取得したFAXの対象外です")
        await self.current_source(run, item)
        if item["state"] == "approved" and order_id is not None:
            basis = await self.capture(run, item)
            wf = basis["workflow"]
            if wf["revision"] != item["decision"].get("approved_revision") or basis["signature"] != item["decision"].get("native_signature"):
                await run_blocking(self.set, run["id"], item["fax_id"], state="ready", review=None, decision=None)
                raise Conflict("承認後に業務の版が変わりました。再確認が必要です")
        if name == "hospital_start_fax" and args.body != {"sha256": item["snapshot"]["sha256"]}:
            raise Conflict("依頼対象の原本ハッシュが必要です")
        if run.get("task_id")=="hospital_review_faxes" and name in ("hospital_accept_recognition","hospital_retry_fax"):
            return  # Exact recovery inputs require the separate TaskFlow human approval.
        if name in ("hospital_accept_recognition", "hospital_review_sheet", "hospital_save_sheet", "hospital_bag_order", "hospital_confirm_order"):
            if item["state"] != "approved" or not item["review"]:
                raise Conflict("このFAXの利用者承認が必要です。エージェント自身は承認できません")
            if name == "hospital_accept_recognition":
                raise Conflict("認識失敗の復旧は個別確認が必要です。この承認は数量シートの確定専用です")
            if name in ("hospital_review_sheet", "hospital_save_sheet") and args.body.get("sheet") != item["review"]["sheet"]:
                raise Conflict("承認された数量シートと変更内容が一致しません")
            wf = (await self.get(run, f"/orders/{order_id}/workflow-v2")).json()
            current = await self.capture(run, item, proposed_sheet=item["review"]["sheet"] if not wf.get("saved_sheet_id") or name in ("hospital_review_sheet", "hospital_save_sheet") else None)
            if current["signature"] != item["review"]["signature"]:
                await run_blocking(self.set, run["id"], item["fax_id"], state="ready", review=None, decision=None)
                raise Conflict("承認後に原本・採用OCR・数量が変わりました。再承認が必要です")

    async def after_write(self, run, name, args, receipt):
        if self.active(run) and name=="hospital_retry_fax":
            item=await run_blocking(self.item, run["id"], str(args.ids["uploaded_pdf_id"]))
            if item:
                snap={**item["snapshot"],"order_id":None,"document_id":None}
                await run_blocking(self.set, run["id"],item["fax_id"],snapshot=snap,state="processing",review=None,decision=None)
            return
        if not self.active(run) or "order_id" not in args.ids:
            return
        item = await run_blocking(self.for_order, run["id"], args.ids["order_id"])
        if not item or item["state"] != "approved":
            return
        from .catalog import OPERATIONS
        current = await self.capture(run, item, proposed_sheet=item["review"]["sheet"] if name not in ("hospital_save_sheet", "hospital_bag_order", "hospital_confirm_order") else None)
        if OPERATIONS[name].versioned:
            owned = receipt.get("workflow", receipt)
            expected = owned.get("revision") if isinstance(owned, dict) else None
            if type(expected) is not int or expected < item["decision"]["approved_revision"]:
                raise Conflict("業務APIの操作後の版を照合できません")
        else:
            expected = item["decision"]["approved_revision"]
        if current["revision"] != expected:
            await run_blocking(self.set, run["id"], item["fax_id"], state="ready", review=None, decision=None)
            raise Conflict("承認した操作の間に業務の版が変わりました。再確認が必要です")
        if current["signature"] != item["review"]["signature"]:
            await run_blocking(self.set, run["id"], item["fax_id"], state="ready", review=None, decision=None)
            raise Conflict("承認した内容と保存結果が一致しません。再確認が必要です")
        basis = await self.capture(run, item)
        if basis["revision"] != current["revision"]:
            raise Conflict("保存結果の照合中に版が変わりました")
        decision = {**item["decision"], "approved_revision": current["revision"], "native_signature": basis["signature"]}
        await run_blocking(self.set, run["id"], item["fax_id"], decision=decision)

    def finish_gate(self, run_id, artifact_ids):
        run = self.store.get(run_id)
        if not self.active(run):
            return
        with self.store.read() as db:
            if not db.execute("SELECT 1 FROM fax_batches WHERE run_id=?", (run_id,)).fetchone():
                raise Conflict("未処理FAXを取得していません")
            rows = db.execute("SELECT rowid,request,result FROM commands WHERE run_id=? AND status='done'", (run_id,)).fetchall()
        for item in self.items(run_id):
            if item["state"] != "completed" or not item["decision"] or not item["decision"].get("approve"):
                raise Conflict("未確定または未承認のFAXが残っています")
            confirms = [row["rowid"] for row in rows if json.loads(row["request"])["name"] == "hospital_confirm_order" and
                str(json.loads(row["request"])["arguments"].get("parameters", {}).get("ids", {}).get("order_id")) == str(item["snapshot"]["order_id"]) and json.loads(row["result"]).get("ok")]
            if not confirms:
                raise Conflict("対象FAXの確定実行の証跡がありません")
            for name in ("hospital_export_labels", "hospital_export_delivery_notes", "hospital_export_aggregate"):
                if not any(row["rowid"] > max(confirms) and json.loads(row["request"])["name"] == name and
                    str(json.loads(row["request"])["arguments"].get("parameters", {}).get("query", {}).get("order_id")) == str(item["snapshot"]["order_id"]) and
                    json.loads(row["result"]).get("artifact", {}).get("id") in artifact_ids for row in rows):
                    raise Conflict("対象FAXの確定後帳票が揃っていません")
