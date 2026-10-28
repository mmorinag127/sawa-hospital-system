from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import and_, delete, func, select

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.models.ingest_job import IngestJob
from src.models.menu import MenuItem, MenuMaster, MonthlyMenu, WeeklyMenu
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.output import ManufacturingAggregateRow
from src.models.uploaded_pdf import UploadedPdf, UploadedPdfAttempt
from src.services.storage_service import load_bytes_from_uri
from src.services import order_cleanup_manifest_v1 as frozen_plan
from src.services.order_retention_policy import LEAF_ORDER_MODELS, unreferenced_order_predicates


_CANONICAL_TABLES_TO_PRESERVE: list[tuple[str, Any]] = [
    ("facilities", Facility),
    ("facility_areas", FacilityArea),
    ("facility_configs", FacilityConfig),
    ("weekly_menus", WeeklyMenu),
    ("monthly_menus", MonthlyMenu),
    ("menu_items", MenuItem),
    ("menu_masters", MenuMaster),
]

_LEAF_MODELS = [(model.__tablename__, model, model.order_id) for model in LEAF_ORDER_MODELS]


@dataclass(frozen=True)
class CleanupScope:
    all_orders: bool = False
    received_from: date | None = None
    received_to: date | None = None


def _received_bounds(scope: CleanupScope) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(scope.received_from, time.min) if scope.received_from else None
    end = datetime.combine(scope.received_to, time.max) if scope.received_to else None
    return start, end


def _order_scope_filter(scope: CleanupScope) -> list[Any]:
    filters: list[Any] = []
    start, end = _received_bounds(scope)
    if start is not None:
        filters.append(Order.received_at >= start)
    if end is not None:
        filters.append(Order.received_at <= end)
    return filters


def _count_model(session: Any, model: Any, predicate: Any | None = None) -> int:
    query = select(func.count()).select_from(model)
    if predicate is not None:
        query = query.where(predicate)
    return int(session.execute(query).scalar_one_or_none() or 0)


def _begin_cleanup_snapshot(session, *, readonly):
    connection = session.connection()
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY' if readonly
                                  else 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
    elif connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN' if readonly else 'BEGIN IMMEDIATE')


def _database_identity(session):
    connection = session.connection()
    binding = session.get_bind()
    engine = getattr(binding, 'engine', binding)
    identity = {'url': engine.url.render_as_string(hide_password=False)}
    if connection.dialect.name == 'postgresql':
        explicit = connection.get_execution_options().get('sawa_cleanup_source_identity')
        if not engine.url.host and not engine.url.query.get('host') and not explicit:
            raise ValueError('cleanup_source_database_identity_required')
        identity['configured_source'] = explicit
        identity['server'] = list(connection.exec_driver_sql(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port(), "
            "(SELECT oid FROM pg_database WHERE datname=current_database())").one())
        if explicit is not None and (not isinstance(explicit, dict) or set(explicit) != {'instance', 'database', 'user'}
                or explicit['database'] != identity['server'][0]):
            raise ValueError('cleanup_source_database_identity_mismatch')
    elif connection.dialect.name == 'sqlite':
        location = connection.exec_driver_sql('PRAGMA database_list').all()
        identity['files'] = []
        for _number, name, path in location:
            if name == 'temp':
                continue
            if path:
                info = Path(path).stat()
                identity['files'].append([name, str(Path(path).resolve()), info.st_dev, info.st_ino])
            else:
                identity['files'].append([name, id(engine), id(connection.connection.driver_connection)])
    else:
        raise ValueError('cleanup_database_dialect_unsupported')
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def build_order_cleanup_plan(scope: CleanupScope) -> dict[str, Any]:
    if not scope.all_orders and scope.received_from is None and scope.received_to is None:
        raise ValueError("cleanup scope must specify all_orders or a received date range")

    with session_scope() as session:
        _begin_cleanup_snapshot(session, readonly=True)
        conditions = [] if scope.all_orders else _order_scope_filter(scope)
        target_ids = select(Order.id).where(*conditions)
        protected = _count_model(session, Order, and_(Order.id.in_(target_ids), ~and_(*unreferenced_order_predicates())))
        counts = {'orders': _count_model(session, Order, Order.id.in_(target_ids))}
        for name, model, owner in _LEAF_MODELS:
            counts[name] = _count_model(session, model, owner.in_(target_ids))
        preserve_counts = {
            table_name: _count_model(session, model)
            for table_name, model in [*_CANONICAL_TABLES_TO_PRESERVE,
                ('order_documents', OrderDocument), ('uploaded_pdfs', UploadedPdf),
                ('uploaded_pdf_attempts', UploadedPdfAttempt), ('ocr_jobs', OcrJob),
                ('ingest_jobs', IngestJob), ('manufacturing_aggregate_rows', ManufacturingAggregateRow)]
        }
        header = {'schema': 1, 'database_sha256': _database_identity(session), 'created_at': datetime.utcnow().isoformat(),
            'protected_orders': protected, 'scope': {
            "all_orders": scope.all_orders,
            "received_from": scope.received_from.isoformat() if scope.received_from else None,
            "received_to": scope.received_to.isoformat() if scope.received_to else None,
            }, 'counts': counts, 'preserved_canonical_counts': preserve_counts}
        queries = [(Order, select(*frozen_plan.columns(Order)).where(*conditions))]
        if not protected:
            queries.extend((model, select(*frozen_plan.columns(model)).where(owner.in_(target_ids)))
                           for _name, model, owner in _LEAF_MODELS)
        manifest = frozen_plan.create(session, header, queries)
        preview = [{'id': row.id, 'received_at': row.received_at.isoformat() if row.received_at else None}
            for row in session.execute(select(Order.id, Order.received_at).where(*conditions)
                .order_by(Order.received_at.asc(), Order.id.asc()).limit(25))]
    return {**header, 'manifest': manifest, 'preview_orders': preview, 'blocked': bool(protected),
            'total_rows_targeted': int(sum(counts.values()))}


def _safe_filename(order_id: str, uri: str, index: int) -> str:
    suffix = Path(urlparse(uri).path).suffix or ".pdf"
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:10]
    identity = hashlib.sha256(order_id.encode('utf-8')).hexdigest()[:16]
    return f"{index:03d}_{identity}_{digest}{suffix}"


def read_order_cleanup_plan(plan_path: str | Path, expected_plan_sha256: str) -> dict[str, Any]:
    with frozen_plan.open_verified(plan_path, expected_plan_sha256) as (manifest, header):
        preview = []
        for (body,) in manifest.execute("SELECT row_json FROM rows WHERE table_name='orders' ORDER BY key_json LIMIT 25"):
            row = json.loads(body)
            timestamp = row.get('received_at')
            preview.append({'id': row['id'], 'received_at': timestamp.get('value') if isinstance(timestamp, dict) else timestamp})
        return {**header, 'manifest': {'path': str(plan_path), 'sha256': expected_plan_sha256, 'schema': 1},
            'preview_orders': preview, 'blocked': header.get('protected_orders') != 0,
            'total_rows_targeted': sum(header['counts'].values())}


def export_order_pdfs_for_cleanup(
    *,
    scope: CleanupScope,
    output_dir: str | Path,
    plan_path: str | Path | None = None,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    if plan_path is None:
        plan = build_order_cleanup_plan(scope)
        plan_path, expected_plan_sha256 = plan['manifest']['path'], plan['manifest']['sha256']
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    import tempfile
    export_dir = Path(tempfile.mkdtemp(prefix='cleanup-export-', dir=target_dir))
    receipts = export_dir / 'items.jsonl'
    exported = failed = missing = 0
    preview = []
    with frozen_plan.open_verified(plan_path, expected_plan_sha256) as (database, header), receipts.open('w', encoding='utf-8') as output:
        expected_scope = {'all_orders': scope.all_orders,
            'received_from': scope.received_from.isoformat() if scope.received_from else None,
            'received_to': scope.received_to.isoformat() if scope.received_to else None}
        if header.get('scope') != expected_scope:
            raise ValueError('cleanup_scope_does_not_match_reviewed_plan')
        for index, (body,) in enumerate(database.execute("SELECT row_json FROM rows WHERE table_name='orders' ORDER BY key_json"), start=1):
            order = json.loads(body)
            order_id, uri = order['id'], order['document_uri']
            if not isinstance(uri, str) or not uri.strip():
                missing += 1
                continue
            target = export_dir / _safe_filename(order_id, uri, index)
            item = {'order_id': order_id, 'source_uri': uri, 'target_path': str(target), 'bytes': 0, 'error': None}
            try:
                parsed = urlparse(uri)
                if parsed.scheme in ('', 'file'):
                    shutil.copyfile(Path(parsed.path if parsed.scheme else uri), target)
                else:
                    target.write_bytes(load_bytes_from_uri(uri))
                item.update(bytes=target.stat().st_size, status='exported', sha256=frozen_plan.digest_file(target))
                exported += 1
            except Exception as error:
                item.update(status='failed', error=type(error).__name__)
                failed += 1
            output.write(json.dumps(item, ensure_ascii=True) + '\n')
            if len(preview) < 25:
                preview.append(item)
    manifest = {'exported_at': datetime.utcnow().isoformat(), 'scope': header['scope'],
        'plan_sha256': expected_plan_sha256, 'items_receipt': {'path': str(receipts), 'sha256': frozen_plan.digest_file(receipts)},
        'preview_items': preview, 'exported_count': exported, 'failed_count': failed, 'missing_reference_count': missing,
        'source_proof': 'frozen_database_metadata; exported bytes hashed at copy time'}
    summary = export_dir / 'manifest.json'
    summary.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding='utf-8')
    return {**manifest, 'manifest_path': str(summary)}


def apply_order_cleanup(scope: CleanupScope, *, confirm_token: str, plan_path: str | Path,
                        expected_plan_sha256: str) -> dict[str, Any]:
    if confirm_token != "CLEAN_ORDER_DATA":
        raise ValueError("confirm_token must be CLEAN_ORDER_DATA")
    expected_scope = {'all_orders': scope.all_orders,
        'received_from': scope.received_from.isoformat() if scope.received_from else None,
        'received_to': scope.received_to.isoformat() if scope.received_to else None}
    removed: dict[str, int] = {name: 0 for name, _model, _owner in _LEAF_MODELS}
    removed['orders'] = 0
    with frozen_plan.open_verified(plan_path, expected_plan_sha256) as (manifest, header), session_scope() as session:
        if header.get('scope') != expected_scope:
            raise ValueError('cleanup_scope_does_not_match_reviewed_plan')
        if header.get('protected_orders') != 0:
            raise ValueError('cleanup_saved_history_is_retained')
        _begin_cleanup_snapshot(session, readonly=False)
        if header.get('database_sha256') != _database_identity(session):
            raise ValueError('cleanup_database_does_not_match_reviewed_plan')
        if session.get_bind().dialect.name == 'postgresql':
            session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('sawa_order_cleanup_manifest_v1',0))")
        # Validate every row before the first delete; all locks last until commit.
        for identities in frozen_plan.order_batches(manifest):
            frozen_plan.require_current_rows(session, manifest, Order, Order.id, identities)
            protected = session.execute(select(Order.id).where(Order.id.in_(identities),
                ~and_(*unreferenced_order_predicates())).limit(1)).first()
            if protected is not None:
                raise ValueError('cleanup_saved_history_is_retained')
            for _name, model, owner in _LEAF_MODELS:
                frozen_plan.require_current_rows(session, manifest, model, owner, identities)
        for identities in frozen_plan.order_batches(manifest):
            for name, model, owner in _LEAF_MODELS:
                result = session.execute(delete(model).where(owner.in_(identities)))
                removed[name] += int(result.rowcount or 0)
            result = session.execute(delete(Order).where(Order.id.in_(identities)))
            removed['orders'] += int(result.rowcount or 0)
        if header.get('counts') != removed:
            raise ValueError('cleanup_reviewed_row_counts_changed')
    return {**header, 'manifest': {'path': str(plan_path), 'sha256': expected_plan_sha256},
            'applied': True, 'removed': removed}
