from datetime import date


class PriceScopeError(ValueError):
    pass


def require_facility(session, facility_id):
    from src.models.school_lunch import Facility

    if type(facility_id) is not int or facility_id <= 0:
        raise PriceScopeError("学校IDが未指定です。要確認のため操作を停止しました。")
    facility = session.get(Facility, facility_id)
    if facility is None:
        raise PriceScopeError("指定学校がFacilityに存在しません。要確認のため操作を停止しました。")
    return facility


def require_same_facility(stored_facility_id, facility_id):
    if stored_facility_id is None:
        raise PriceScopeError("学校未割当の既存データです。原本を保持し、要確認のため操作を停止しました。")
    if type(stored_facility_id) is not type(facility_id) or stored_facility_id != facility_id:
        raise PriceScopeError("選択学校とデータの学校IDが一致しません。操作を停止しました。")


def menu_source_ids(payload):
    values = payload.get("source_menu_execution_ids", [])
    if not isinstance(values, list) or any(type(value) is not int or value <= 0 for value in values):
        raise PriceScopeError("採用献立実施IDの一覧が不正です。")
    singular = payload.get("source_menu_execution_id")
    if singular is not None and (type(singular) is not int or singular <= 0):
        raise PriceScopeError("採用献立実施IDが不正です。")
    return sorted(set(values + ([singular] if singular is not None else [])))


def validate_price_scope(session, payload, *, for_adoption=True):
    from sqlalchemy import inspect
    from src.models.school_lunch import Facility, MenuExecution
    from src.services.implementation_price_ocr_job_service import pending_ocr_source
    with session.no_autoflush:
        if for_adoption:
            menu_ids = menu_source_ids(payload)
            facility_id = payload.get("facility_id")
            for row in session.new | session.dirty | session.deleted:
                targets = [facility_id] if isinstance(row, Facility) else menu_ids if isinstance(row, MenuExecution) else []
                if targets:
                    state = inspect(row)
                    attribute = state.attrs.id
                    values = list(attribute.history.added) + list(attribute.history.deleted) + [attribute.loaded_value]
                    if state.identity:
                        values.append(state.identity[0])
                    if any(type(value) is type(target) and value == target for value in values for target in targets):
                        raise PriceScopeError("未保存の正本変更があります。出典採用を停止しました。")
            job_ids = payload.get("source_ocr_job_ids", [])
            if isinstance(job_ids, list) and any(pending_ocr_source(session, job_id) for job_id in job_ids if isinstance(job_id, str)):
                raise PriceScopeError("未保存のOCR正本変更があります。出典採用を停止しました。")
        return _validate_price_scope(session, payload, for_adoption=for_adoption)


def _validate_price_scope(session, payload, *, for_adoption):
    from src.models.school_lunch import MenuExecution
    from src.models.implementation_price_ocr_job import ImplementationPriceOcrJob
    from src.services.implementation_price_ocr_job_service import validated_successful_result
    from src.services.implementation_price_ocr_execution_guard import same_json

    facility = require_facility(session, payload.get("facility_id"))
    schools = payload.get("schools")
    if not isinstance(schools, list) or len(schools) != 1:
        raise PriceScopeError("実施価格表は選択学校1校が必須です。複数校原本は分割せず要確認で停止します。")
    if not isinstance(schools[0], dict) or schools[0].get("name") != facility.name:
        raise PriceScopeError("実施価格表の学校名が選択学校のFacilityと一致しません。")
    try:
        implementation_date = date.fromisoformat(payload["implementation_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PriceScopeError("実施日をYYYY-MM-DDで指定してください。") from exc
    menu_ids = menu_source_ids(payload)
    for menu_id in menu_ids:
        if for_adoption:
            from sqlalchemy import select, update
            with session.no_autoflush:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(update(MenuExecution).where(MenuExecution.id == menu_id)
                        .values(id=MenuExecution.id).execution_options(synchronize_session=False))
                menu = session.execute(select(MenuExecution).where(MenuExecution.id == menu_id)
                    .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        else:
            menu = session.get(MenuExecution, menu_id)
        if menu is None:
            raise PriceScopeError("採用した献立実施が存在しません。要確認で停止しました。")
        require_same_facility(menu.facility_id, facility.id)
        if menu.service_date != implementation_date:
            raise PriceScopeError("採用した献立実施の日付が実施価格の日付と一致しません。")
    job_ids = payload.get("source_ocr_job_ids", [])
    if not isinstance(job_ids, list) or any(not isinstance(value, str) or not value.strip() for value in job_ids):
        raise PriceScopeError("採用OCRジョブIDの一覧が不正です。")
    results = {}
    for job_id in job_ids:
        job = session.get(ImplementationPriceOcrJob, job_id)
        if job is None:
            raise PriceScopeError("採用OCRジョブが存在しません。要確認で停止しました。")
        require_same_facility(job.facility_id, facility.id)
        if job.document_type != "invoice":
            raise PriceScopeError("実施価格に採用できるOCRは納品書(invoice)のみです。要確認で停止しました。")
        if job.status != "succeeded":
            raise PriceScopeError("採用OCRジョブが完了していません。要確認で停止しました。")
        try:
            results[job_id] = validated_successful_result(session, job_id, facility_id=facility.id, document_type="invoice", for_adoption=for_adoption)
        except ValueError as exc:
            raise PriceScopeError(str(exc)) from exc
    for cost in payload.get("cost_items", []):
        job_id = cost.get("source_ocr_job_id")
        if job_id is None:
            continue
        if job_id not in job_ids:
            raise PriceScopeError("原価明細のOCR出典が採用ジョブ一覧にありません。")
        if cost.get("invoice_school_confirmed") is not True:
            raise PriceScopeError("この納品明細が選択学校のものか原本で確認してください。")
        tables = results[job_id].get("invoice_tables", [])
        matches = [table for table in tables if same_json(table.get("table_id"), cost.get("source_table_id")) and same_json(table.get("page_index"), cost.get("source_page_index"))]
        index = cost.get("source_row_index")
        if len(matches) != 1 or type(index) is not int or index < 0 or index >= len(matches[0].get("line_items", [])):
            raise PriceScopeError("採用した納品明細の原本ページ・表・行を確認できません。")
        original = matches[0]["line_items"][index]
        evidence = cost.get("invoice_evidence")
        if not isinstance(evidence, dict) or any((key in evidence) != (key in original) or not same_json(evidence.get(key), original.get(key)) for key in ("raw_quantity", "raw_unit_price", "raw_amount", "raw_cells")):
            raise PriceScopeError("OCR原文は変更できません。修正値と原文を分けて保存してください。")
    return implementation_date
