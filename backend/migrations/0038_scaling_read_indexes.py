"""Index dated reads and per-owner history without changing stored values."""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_order_documents_storage_uri", "order_documents", ["storage_uri"]),
    ("ix_order_documents_source_email_id", "order_documents", ["source_email_id"]),
    ("ix_order_documents_retention", "order_documents", ["received_at", "id"]),
    ("ix_orders_current_document_id", "orders", ["current_document_id"]),
    ("ix_orders_document_uri", "orders", ["document_uri"]),
    ("ix_menu_masters_name_id", "menu_masters", ["name", "id"]),
    ("ix_audit_logs_created_id", "audit_logs", ["created_at", "id"]),
    ("ix_audit_logs_action_target_created_id", "audit_logs", ["action", "target", "created_at", "id"]),
    ("ix_audit_logs_menu_scope_latest", "audit_logs", ["action", "target", "menu_scope_kind", "created_at", "id"]),
    ("ix_order_lines_date_order", "order_lines", ["date", "order_id"]),
    ("ix_shipping_tracking_logs_key_latest", "shipping_tracking_logs", ["tracking_key", "looked_up_at", "created_at", "id"]),
    ("ix_order_ocr_evidence_runs_order_created_id", "order_ocr_evidence_runs", ["order_id", "created_at", "id"]),
    ("ix_order_ocr_evidence_runs_template_history", "order_ocr_evidence_runs", ["order_id", "template_version_id", "created_at", "id"]),
    ("ix_order_output_bundles_order_created_id", "order_output_bundles", ["order_id", "created_at", "id"]),
)


def upgrade():
    from src.models.ocr_display_projection_v1 import expression
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    from src.models.menu_upload_scope_v1 import scope_kind
    from src.models.dashboard_counts_v1 import install as install_dashboard_counts
    from src.models.upload_visibility_v1 import unresolved_upload_conditions
    from src.models.document_reference_guard_v1 import install as install_reference_guards

    op.create_table('order_dashboard_counts',
        sa.Column('group_key', sa.String(), primary_key=True), sa.Column('source_kind', sa.String(20), nullable=False),
        sa.Column('facility', sa.String()), sa.Column('week', sa.String()), sa.Column('status', sa.String()),
        sa.Column('row_count', sa.BigInteger(), nullable=False),
        sa.CheckConstraint('row_count >= 0', name='ck_dashboard_count_nonnegative'))
    install_dashboard_counts(op.get_bind(), populate=True)
    uploads = sa.table('uploaded_pdfs', *(sa.column(name) for name in ('status', 'current_order_id', 'current_document_id')))
    visible_upload = sa.and_(*unresolved_upload_conditions(uploads.c))
    op.create_index('ix_uploaded_pdfs_unresolved_received_id', 'uploaded_pdfs', ['received_at', 'id'],
                    sqlite_where=visible_upload, postgresql_where=visible_upload)
    op.add_column('audit_logs', sa.Column('menu_scope_kind', sa.String(12), nullable=True, server_default='invalid'))
    audit = sa.table('audit_logs', sa.column('id', sa.String()), sa.column('metadata', sa.JSON()),
                     sa.column('menu_scope_kind', sa.String(12)))
    rows = op.get_bind().execute(sa.select(audit.c.id, audit.c.metadata).execution_options(yield_per=1))
    try:
        for row in rows:
            op.get_bind().execute(audit.update().where(audit.c.id == row.id).values(menu_scope_kind=scope_kind(row.metadata)))
    finally:
        rows.close()
    with op.batch_alter_table('audit_logs') as batch:
        batch.alter_column('menu_scope_kind', existing_type=sa.String(12), nullable=False)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION pg_trgm WITH SCHEMA public')
        namespace = op.get_bind().scalar(sa.text(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'"))
        operator = op.get_bind().dialect.identifier_preparer.quote(namespace) + '.gin_trgm_ops'
        op.create_index('ix_menu_masters_name_trgm', 'menu_masters', ['name'],
                        postgresql_using='gin', postgresql_ops={'name': operator})
    op.add_column('ocr_jobs', sa.Column('progress_metrics', sa.JSON(), nullable=True))
    column = sa.Column('display_read_json', sa.Text(), sa.Computed(
        expression(op.get_bind().dialect.name, progress_keys=PROGRESS_KEYS), persisted=True))
    if op.get_bind().dialect.name == 'sqlite':
        computed = [sa.Column(item['name'], item['type'], sa.Computed(**item['computed']),
                              nullable=item['nullable'])
                    for item in sa.inspect(op.get_bind()).get_columns('ocr_jobs') if item.get('computed')]
        with op.batch_alter_table('ocr_jobs', recreate='always') as batch:
            # Re-add generated columns so Alembic does not INSERT their derived values.
            for existing in computed:
                batch.drop_column(existing.name)
            for existing in computed:
                batch.add_column(existing)
            batch.add_column(column)
    else:
        op.add_column('ocr_jobs', column)
    for name, table, columns in INDEXES:
        options = {'postgresql_using': 'hash'} if name in {
            'ix_order_documents_storage_uri', 'ix_order_documents_source_email_id', 'ix_orders_document_uri'} else {}
        op.create_index(name, table, columns, **options)
    op.create_index("ix_users_normalized_account", "users", [sa.text("lower(trim(account))")])
    install_reference_guards(op.get_bind())


def downgrade():
    from src.models.ocr_metrics_v1 import MergeMetrics
    from src.models.dashboard_counts_v1 import uninstall as uninstall_dashboard_counts
    from src.models.document_reference_guard_v1 import uninstall as uninstall_reference_guards

    uninstall_reference_guards(op.get_bind())
    op.drop_index('ix_uploaded_pdfs_unresolved_received_id', table_name='uploaded_pdfs')
    uninstall_dashboard_counts(op.get_bind())
    op.drop_table('order_dashboard_counts')
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('ix_menu_masters_name_trgm', table_name='menu_masters')
        op.execute('DROP EXTENSION pg_trgm')
    op.drop_column('ocr_jobs', 'display_read_json')
    jobs = sa.table('ocr_jobs', sa.column('metrics', sa.JSON()), sa.column('progress_metrics', sa.JSON()))
    op.execute(jobs.update().values(metrics=MergeMetrics(jobs.c.metrics, jobs.c.progress_metrics)))
    op.drop_column('ocr_jobs', 'progress_metrics')
    op.drop_index("ix_users_normalized_account", table_name="users")
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_column('audit_logs', 'menu_scope_kind')
