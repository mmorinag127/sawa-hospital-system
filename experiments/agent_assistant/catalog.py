"""Explicit native API operations. There is no arbitrary URL/SQL/shell tool."""
from dataclasses import dataclass
import re
import hashlib
import json
from urllib.parse import quote


@dataclass(frozen=True)
class Operation:
    domain: str
    method: str
    path: str
    description: str
    versioned: bool = False
    file: bool = False


OPERATIONS = {}


def add(domain, name, method, path, description, **kwargs):
    OPERATIONS[domain.replace("school-lunch", "lunch")+"_"+name] = Operation(domain, method, path, description, **kwargs)


for name, method, path, description in [
    ("list_orders", "GET", "/orders", "注文を検索する。施設・期間を照合し対象IDを確定する。"),
    ("pending_faxes", "GET", "/ingest/pending-faxes", "登録済みの未処理FAXと未確定のOCR完了FAXを取得する。"),
    ("get_fax", "GET", "/ingest/uploads/{uploaded_pdf_id}", "FAXの現在状態と対応する注文・原本IDを確認する。"),
    ("start_fax", "POST", "/ingest/uploads/{uploaded_pdf_id}/start", "取得済みsha256を指定し、登録済み未処理FAXのOCRを開始する。処理中の再実行やleaseリセットはしない。"),
    ("list_facilities", "GET", "/facilities", "正本の施設候補を取得する。複数候補から推測で選ばない。"),
    ("get_facility_versions", "GET", "/facilities/{facility_id}/versions", "正本JSONの版と有効期間を取得し、取込に指定する版を照合する。"),
    ("resolve_facility", "GET", "/facilities/{facility_id}/resolved", "明示したeffective_dateに有効な正本施設・テンプレートを解決する。"),
    ("list_monthly_menus", "GET", "/monthly-menus", "登録済み月次メニューと対象月IDを照合する。"),
    ("get_monthly_menu", "GET", "/monthly-menus/{month_id}", "対象月の保存済みメニュー・数量対応・版を確認する。"),
    ("get_menu_scope_options", "GET", "/monthly-menus/scope-options", "月次メニューの対象候補を取得する。先頭候補を自動選択しない。"),
    ("get_order", "GET", "/orders/{order_id}", "注文の保存済み状態を取得する。"),
    ("get_workflow", "GET", "/orders/{order_id}/workflow-v2", "現在の版・採用OCR・blockerを取得する。更新前に必ず読む。"),
    ("get_recognition_review", "GET", "/orders/{order_id}/workflow-v2/recognition-review", "原本に対応する認識値・確認箇所を取得する。"),
    ("get_sheet", "GET", "/orders/{order_id}/workflow-v2/sheet", "保存済みの数量シートを取得する。"),
    ("get_sheet_source", "GET", "/orders/{order_id}/workflow-v2/sheet-source", "採用済みOCRからシート候補を取得する。保存済み編集へ自動上書きしない。"),
    ("get_ocr_results", "GET", "/orders/{order_id}/workflow-v2/ocr-results", "OCR候補の原本・実行系譜を取得する。"),
    ("get_document", "GET", "/orders/{order_id}/document", "確認対象の原本PDFを取得する。"),
    ("get_uploads", "GET", "/ingest/uploads", "取込ジョブ・重複・完了状態を照会する。"),
    ("pre_save", "POST", "/orders/{order_id}/workflow-v2/sheet/pre-save-status", "指定数量シートの保存前検査を実行する。"),
]:
    add("hospital", name, method, path, description)
for name, path, description in [
    ("set_context", "context", "施設・週・正本テンプレートを明示して設定する。推測や既定候補は禁止。"),
    ("run_ocr", "ocr-runs", "原本・施設・対象週が確認済みの場合にOCRを開始する。受付だけを成功としない。"),
    ("select_ocr", "ocr-results/{ocr_result_id}/select", "照合済みOCR候補を明示して採用する。"),
    ("review_sheet", "sheet/review-confirm", "実際に確認したシートと根拠を渡して確認記録を保存する。"),
    ("check_anomalies", "sheet/anomaly-review", "数量異常を検査する。認識値を推測で修正しない。"),
    ("bag_order", "bagging", "保存数量から既存の袋分け処理を実行する。"),
    ("confirm_order", "confirm", "検査と照合を完了した版の注文を確定する。"),
]:
    add("hospital", name, "POST", "/orders/{order_id}/workflow-v2/"+path, description, versioned=True)
for name in ("quad-review", "header-axis-review", "row-axis-review"):
    add("hospital", "get_"+name.replace("-", "_"), "GET", "/orders/{order_id}/workflow-v2/"+name, "原本の確認箇所と保存済み判断を取得する。")
    add("hospital", "save_"+name.replace("-", "_"), "PUT", "/orders/{order_id}/workflow-v2/"+name, "原本で確認した範囲・軸の判断を保存する。未確認のまま承認しない。", versioned=True)
add("hospital", "get_facility_config", "GET", "/orders/{order_id}/workflow-v2/facility-config", "この注文に有効な正本施設・テンプレート設定を取得する。")
add("hospital", "accept_recognition", "PUT", "/orders/{order_id}/workflow-v2/recognition-review/accept", "原本を確認した認識値を採用する。確認者はAPI認証の利用者として記録する。", versioned=True)
add("hospital", "save_sheet", "PUT", "/orders/{order_id}/workflow-v2/sheet", "確認した数量を保存する。operator編集と原本を保持する。", versioned=True)
add("hospital", "save_recognition", "PUT", "/orders/{order_id}/workflow-v2/recognition-review", "原本で確認できた認識訂正だけを保存する。", versioned=True)
add("hospital", "upload_fax", "POST", "/ingest/upload", "施設・週と一致を確認した登録済み添付PDFを取り込む。force・skip_ocrは禁止。", file=True)
add("hospital", "review_faxes", "GET", "/ingest/review-faxes", "要確認・失敗したFAXを全件取得する。")
add("hospital", "retry_fax", "POST", "/ingest/uploads/{uploaded_pdf_id}/retry", "人が承認した登録済み原本の再処理。原本・施設・週を再照合する。")
for name, path in [("labels", "/outputs/labels.xlsx"), ("delivery_notes", "/outputs/delivery-notes"), ("aggregate", "/outputs/manufacturing-aggregate")]:
    add("hospital", "export_"+name, "GET", path, "対象注文・版を照合した帳票を取得する。原本の確認完了を代用しない。")

add("shift", "get_period", "GET", "/planning/period", "選択月に対する正規の勤務期間を設定から解決する。暦月の初日を推測しない。")
add("shift", "get_constraint_dates", "GET", "/planning/constraint-date-counts", "登録済み予定の日別件数を200日ずつ取得する。予定本文は取得しない。")
for name, path in [("staff", "/staff"), ("settings", "/optimization-settings"), ("constraints", "/constraints"), ("overview", "/planning/overview"),
                   ("demands", "/planning/demands"), ("workdays", "/planning/workdays"),
                   ("templates", "/planning/shift-templates"), ("positions", "/planning/positions"),
                   ("runs", "/solve-runs"), ("run", "/solve-runs/{run_id}"),
                   ("validation", "/candidates/{candidate_id}/validation")]:
    add("shift", "get_"+name, "GET", path, "保存済みシフトの"+name+"を取得する。対象期間と候補ID・入力版を確認する。")
for name, method, path in [
    ("create_staff", "POST", "/staff"), ("update_staff", "PUT", "/staff/{staff_id}"),
    ("save_constraint", "POST", "/constraints"), ("update_constraint", "PUT", "/constraints/{constraint_id}"),
    ("save_demands", "PUT", "/planning/demands"), ("save_workdays", "PUT", "/planning/workdays"),
    ("preflight", "POST", "/rule-preflight"), ("propose_rules", "POST", "/rule-assistant/candidates"),
    ("apply_rules", "POST", "/rule-assistant/apply"), ("solve", "POST", "/solve"),
    ("edit_cell", "PUT", "/candidates/{candidate_id}/cells/{staff_id}/{work_date}"),
    ("lock_assignment", "PUT", "/assignments/{assignment_id}/lock"),
    ("confirm", "POST", "/planning/solve-runs/{run_id}/confirm"),
]:
    add("shift", name, method, path, "既存シフトAPIで"+name+"を実行する。版と候補のpreconditionをそのまま渡す。勤務案はOR-Toolsで生成し手書きしない。")
add("shift", "save_settings", "PUT", "/optimization-settings", "利用者が確認した勤務条件の変更だけを、取得済み設定版に対して保存する。")
add("shift", "export", "GET", "/solve-runs/{run_id}/export", "明示した候補のシフト帳票を取得する。")

prefix = "/api/school-lunch"
for name, path in [("schools", "/facilities"), ("menus", "/menu-executions"),
                   ("month", "/monthly-overview"), ("menu", "/menu-executions/{menu_id}"),
                   ("process", "/menu-executions/{menu_id}/process"),
                   ("process_route_options", "/menu-executions/{menu_id}/process/route-options"),
                   ("process_drawing", "/menu-executions/{menu_id}/process/question-drawing"),
                   ("schedules", "/work-schedules"), ("schedule", "/work-schedules/{schedule_id}"),
                   ("routes", "/route-diagrams"), ("layouts", "/route-diagrams/layouts"),
                   ("procurement", "/monthly-procurement"), ("import_job", "/cooking-arrangements/jobs/{job_id}"),
                   ("source_review", "/cooking-arrangements/jobs/{job_id}/review")]:
    add("school-lunch", "get_"+name, "GET", prefix+path, "学校・期間を明示して"+name+"を取得する。他校・別月の入力を流用しない。")
for name, method, path in [
    ("confirm_import", "POST", "/cooking-arrangements/confirm"),
    ("save_menu", "PUT", "/menu-executions/{menu_id}"),
    ("infer_process", "POST", "/menu-executions/{menu_id}/process/infer"),
    ("save_process", "PUT", "/menu-executions/{menu_id}/process"),
    ("create_schedule", "POST", "/work-schedules"), ("save_schedule", "PUT", "/work-schedules/{schedule_id}"),
    ("generate_route", "POST", "/route-diagrams/generate"),
    ("save_route", "PUT", "/route-diagrams/{diagram_id}"),
    ("save_procurement", "PUT", "/monthly-procurement"),
]:
    add("school-lunch", name, method, prefix+path, "既存学校給食APIで"+name+"を実行する。未確定数量・単位・担当・アレルギーを推測しない。版と学校IDを保持する。")
add("school-lunch", "get_schedule_draft", "GET", prefix+"/work-schedules/draft", "選択した学校・日の正規の工程表案を既存生成処理で取得する。作業時間や担当を推測しない。")
add("school-lunch", "get_source_document", "GET", prefix+"/cooking-arrangements/documents/{document_id}", "添付原本の確定前抽出結果を保存状態から再取得する。")
add("school-lunch", "get_source_documents", "GET", prefix+"/cooking-arrangements/documents", "学校・月に一致する取込原本一覧を取得する。")
add("school-lunch", "save_source_review", "PUT", prefix+"/cooking-arrangements/jobs/{job_id}/review", "人が承認した原本行の確認記録を保存する。")
add("school-lunch", "import_source", "POST", prefix+"/cooking-arrangements/extract", "学校と期間を確認した添付原本を抽出する。確認保存とは別の処理。", file=True)
for name, path in [("schedule", "/work-schedules/{schedule_id}/export"), ("month_schedules", "/work-schedules/monthly/export"),
                   ("route", "/route-diagrams/{diagram_id}/export"), ("month_routes", "/route-diagrams/export/month"),
                   ("procurement", "/monthly-procurement/export")]:
    add("school-lunch", "export_"+name, "GET", prefix+path, "学校・日付・最新献立版と一致した帳票を取得する。欠落日は完了扱いにしない。")

for name, method, path in [
    ('get_price_quantities','GET','/implementation-price-tables/menu-quantities/{menu_id}'),
    ('get_price_drafts','GET','/implementation-price-tables/source-drafts'),
    ('get_price_source','GET','/implementation-price-tables/source-drafts/{draft_id}'),
    ('save_price_source','POST','/implementation-price-tables/source-drafts'),
    ('get_price_result','GET','/implementation-price-tables/source-drafts/{draft_id}/preview'),
    ('export_price','GET','/implementation-price-tables/source-drafts/{draft_id}/export'),
]:
    add('school-lunch',name,method,path,'対象学校・日付・全献立の正本に基づく実施価格を取得・保存・計算・出力する。単価や数量を推測しない。')


def build_path(operation, values):
    required = re.findall(r"\{(\w+)\}", operation.path)
    if set(values) != set(required):
        raise ValueError("対象IDが不足または過剰です: "+",".join(required))
    for value in values.values():
        if not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value)):
            raise ValueError("対象IDの形式が不正です")
    return operation.path.format(**{k: quote(str(v), safe="") for k, v in values.items()})


def public_intake_data(name, value):
    """Redact worker secrets and bind procurement to the exact native result."""
    if name == 'lunch_get_procurement':
        encoded=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",", ":"))
        return {**value,'fingerprint':hashlib.sha256(encoded.encode()).hexdigest()}
    if name not in {'hospital_get_fax','hospital_get_uploads','hospital_retry_fax'}:
        return value
    def clean(item):
        if isinstance(item, dict):
            return {key:clean(child) for key,child in item.items() if key not in {'lease_token','lease_owner','lease_expires_at','storage_uri'}}
        if isinstance(item, list):return [clean(child) for child in item]
        return item
    return clean(value)
