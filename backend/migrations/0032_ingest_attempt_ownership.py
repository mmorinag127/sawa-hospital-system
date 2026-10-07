"""Persist ingest input/attempt ownership and uploaded PDF claim tokens."""

from hashlib import sha256
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    fields = {
        "ingest_jobs": ("input_digest", "generation", "claim_id", "recovery_claim_id", "failure_kind", "generation_attempts"),
        "uploaded_pdfs": ("lease_token",),
    }
    for table, names in fields.items():
        if not inspector.has_table(table):
            raise RuntimeError(f"0032 requires {table}")
        if set(names) & {column["name"] for column in inspector.get_columns(table)}:
            raise RuntimeError(f"0032 unknown existing ownership columns: {table}")
    jobs = sa.Table("ingest_jobs", sa.MetaData(), autoload_with=bind)
    recorded = []
    for row in bind.execute(sa.select(jobs.c.id, jobs.c.payload, jobs.c.status, jobs.c.attempts)).mappings():
        payload = row["payload"]
        digest = None
        if isinstance(payload, dict):
            digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        recorded.append((row["id"], digest, uuid4().hex if row["status"] == "pending" and digest is not None else None,
                         "terminal_unclassified" if row["status"] == "error" else None,
                         row["attempts"] if row["status"] == "pending" and digest is not None else None))
    for table, names in fields.items():
        for name in names:
            op.add_column(table, sa.Column(name, sa.Integer() if name == "generation_attempts" else sa.String(), nullable=True))
    for job_id, digest, generation, failure, attempts in recorded:
        bind.execute(sa.text("UPDATE ingest_jobs SET input_digest=:digest,generation=:generation,failure_kind=:failure,generation_attempts=:attempts WHERE id=:id"),
                     {"id": job_id, "digest": digest, "generation": generation, "failure": failure, "attempts": attempts})


def downgrade():
    raise RuntimeError("0032 ownership fences cannot be discarded or rewound")
