"""Preserve unknown historical timestamps and converge reviewed live constraints."""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

EXTRA_INDEXES = {
    "ocr_training_samples": {"facility_code": False, "message_id": False, "order_id": True},
    "order_ocr_revisions": {"order_id": False},
}
MASTER_FK = "monthly_menu_items_menu_master_id_fkey"


def upgrade():
    bind = op.get_bind()
    for table in ("audit_logs", "users"):
        if bind.dialect.name == "postgresql":
            op.alter_column(table, "created_at", existing_type=sa.DateTime(), nullable=True)
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("created_at", existing_type=sa.DateTime(), nullable=True)
    for table, columns in EXTRA_INDEXES.items():
        existing = {item["name"]: item for item in sa.inspect(bind).get_indexes(table)}
        for column, unique in columns.items():
            name = f"ix_{table}_{column}"
            if name in existing:
                if existing[name]["column_names"] != [column] or existing[name]["unique"] != unique:
                    raise RuntimeError("0035 incompatible existing index: " + name)
            else:
                op.create_index(name, table, [column], unique=unique)
    fks = sa.inspect(bind).get_foreign_keys("monthly_menu_items")
    matching = [fk for fk in fks if fk["name"] == MASTER_FK]
    if matching:
        fk = matching[0]
        if (fk["constrained_columns"] != ["menu_master_id"] or fk["referred_table"] != "menu_masters"
                or fk["referred_columns"] != ["id"] or fk["options"]):
            raise RuntimeError("0035 incompatible menu master foreign key")
    elif bind.dialect.name == "postgresql":
        op.create_foreign_key(MASTER_FK, "monthly_menu_items", "menu_masters", ["menu_master_id"], ["id"])
    else:
        indexes = bind.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='monthly_menu_items' AND sql IS NOT NULL").all()
        with op.batch_alter_table("monthly_menu_items") as batch:
            batch.create_foreign_key(MASTER_FK, "menu_masters", ["menu_master_id"], ["id"])
        present = set(bind.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='monthly_menu_items'").scalars())
        for name, sql in indexes:
            if name not in present:
                bind.exec_driver_sql(sql)


def downgrade():
    raise RuntimeError("0035 preserves historical NULL timestamps; implicit reversal is forbidden")
