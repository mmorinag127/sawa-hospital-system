"""Keep inventory configuration optional for menu drafts, without guessed values."""
from alembic import op
import sqlalchemy as sa

revision = "0017_optional_ingredient_config"
down_revision = "0016_meat_fish_work_zone"
branch_labels = None
depends_on = None

FIELDS = {
    "category": sa.String(100),
    "stock_unit": sa.String(32),
    "order_unit": sa.String(32),
    "conversion_rate": sa.Numeric(18, 6),
}


def upgrade():
    with op.batch_alter_table("school_lunch_ingredients") as batch:
        for name, kind in FIELDS.items():
            batch.alter_column(name, existing_type=kind, nullable=True, server_default=None)
    op.add_column("school_lunch_menu_ingredients", sa.Column(
        "unconfirmed_ingredient_fields", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    bind = op.get_bind()
    missing = " OR ".join(f"{name} IS NULL" for name in FIELDS)
    if bind.execute(sa.text(f"SELECT COUNT(*) FROM school_lunch_ingredients WHERE {missing}")).scalar():
        raise RuntimeError("在庫設定が未入力の食材があるためダウングレードできません")
    if bind.execute(sa.text("SELECT COUNT(*) FROM school_lunch_menu_ingredients "
                            "WHERE CAST(unconfirmed_ingredient_fields AS TEXT) <> '[]'")).scalar():
        raise RuntimeError("未確認の食材設定を持つ献立があるためダウングレードできません")
    with op.batch_alter_table("school_lunch_menu_ingredients") as batch:
        batch.drop_column("unconfirmed_ingredient_fields")
    with op.batch_alter_table("school_lunch_ingredients") as batch:
        for name, kind in FIELDS.items():
            batch.alter_column(name, existing_type=kind, nullable=False)
