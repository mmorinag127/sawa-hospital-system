"""Preserve menu-wide source instructions without rewriting ingredient processes."""
from alembic import op
import sqlalchemy as sa

revision = "0013_menu_cooking_instructions"
down_revision = "0012_school_lunch_hygiene"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("school_lunch_menu_executions", sa.Column("cooking_instructions", sa.Text(), nullable=False, server_default=""))
    op.add_column("school_lunch_menu_executions", sa.Column("cooking_instruction_sources", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("school_lunch_menu_executions", "cooking_instruction_sources")
    op.drop_column("school_lunch_menu_executions", "cooking_instructions")
