"""Persist OCR delivery intent on the existing job transaction boundary."""
from alembic import op
import sqlalchemy as sa

revision = "0027_ocr_delivery_intent"
down_revision = "0026_auth_support_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("implementation_price_ocr_jobs", sa.Column("delivery_json", sa.JSON(), nullable=True))
    # NULL means no durable visit has been reserved; no delivery evidence is inferred.
    op.add_column("implementation_price_ocr_jobs", sa.Column("delivery_visit_count", sa.Integer(), nullable=True))
    jobs = sa.table("implementation_price_ocr_jobs", sa.column("id", sa.String()),
        sa.column("status", sa.String()), sa.column("attempt_count", sa.Integer()),
        sa.column("delivery_json", sa.JSON()))
    connection = op.get_bind()
    # Only queued DB state proves work is awaiting delivery. Transport is unknown.
    for job_id, attempt in connection.execute(sa.select(jobs.c.id, jobs.c.attempt_count).where(jobs.c.status == "queued")):
        if attempt is None or attempt < 0:
            raise RuntimeError("Queued OCR job has invalid attempt count")
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(delivery_json={
            "generation": 1, "expected_attempt_count": attempt, "state": "pending", "mode": None,
        }))


def downgrade():
    jobs = sa.table("implementation_price_ocr_jobs", sa.column("delivery_json", sa.JSON()),
        sa.column("delivery_visit_count", sa.Integer()))
    if op.get_bind().execute(sa.select(jobs.c.delivery_json).where(
            jobs.c.delivery_json.is_not(None) | jobs.c.delivery_visit_count.is_not(None)).limit(1)).first() is not None:
        raise RuntimeError("Cannot discard persisted OCR delivery intents or visits")
    op.drop_column("implementation_price_ocr_jobs", "delivery_json")
    op.drop_column("implementation_price_ocr_jobs", "delivery_visit_count")
