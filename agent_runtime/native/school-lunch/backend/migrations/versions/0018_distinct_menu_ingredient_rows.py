"""Preserve separate uses of the same material as separate menu rows."""
from alembic import op
import sqlalchemy as sa

revision = "0018_distinct_ingredient_rows"
down_revision = "0017_optional_ingredient_config"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("school_lunch_menu_ingredients") as batch:
        batch.drop_constraint("uq_school_lunch_menu_ingredient", type_="unique")


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM school_lunch_menu_ingredients "
        "GROUP BY menu_execution_id, ingredient_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first():
        raise RuntimeError("同じ食材の複数使用行があるためダウングレードできません")
    with op.batch_alter_table("school_lunch_menu_ingredients") as batch:
        batch.create_unique_constraint("uq_school_lunch_menu_ingredient", ["menu_execution_id", "ingredient_id"])
