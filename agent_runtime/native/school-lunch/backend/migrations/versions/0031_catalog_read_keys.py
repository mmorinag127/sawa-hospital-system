"""Index exact names and retain small editor vocabulary outside revision bodies."""
from alembic import op
import sqlalchemy as sa
from src.shared_menu_catalog_keys import catalog_search_text, menu_identity_key, revision_counts, vocabulary
from src.account_identity_v1 import account_identity

revision = '0031_catalog_read_keys'
down_revision = '0030_shared_menus'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('account_lookup', sa.String(), nullable=True))
    connection = op.get_bind()
    users = sa.table('users', sa.column('id', sa.String()), sa.column('account', sa.String()), sa.column('account_lookup', sa.String()))
    cursor = connection.execute(sa.select(users.c.id, users.c.account).execution_options(yield_per=200))
    try:
        for row in cursor:
            connection.execute(users.update().where(users.c.id == row.id).values(account_lookup=account_identity(row.account)))
    finally:
        cursor.close()
    with op.batch_alter_table('users') as batch:
        batch.alter_column('account_lookup', existing_type=sa.String(), nullable=False)
        batch.create_index('ix_users_normalized_account', ['account_lookup'])
    op.add_column('school_lunch_shared_menus', sa.Column('name_identity', sa.String(64)))
    op.add_column('school_lunch_shared_menus', sa.Column('search_name', sa.Text()))
    connection = op.get_bind()
    menus = sa.table('school_lunch_shared_menus', sa.column('id', sa.Integer()),
                     sa.column('name', sa.String()), sa.column('name_identity', sa.String()), sa.column('search_name', sa.Text()))
    cursor = connection.execute(sa.select(menus.c.id, menus.c.name).execution_options(yield_per=200))
    try:
        for row in cursor:
            connection.execute(menus.update().where(menus.c.id == row.id).values(
                name_identity=menu_identity_key(row.name), search_name=catalog_search_text(row.name)))
    finally:
        cursor.close()
    with op.batch_alter_table('school_lunch_shared_menus') as batch:
        batch.alter_column('name_identity', existing_type=sa.String(64), nullable=False)
        batch.alter_column('search_name', existing_type=sa.Text(), nullable=False)
        batch.create_index('ix_school_lunch_shared_menus_name_identity', ['name_identity'])
    op.create_table('school_lunch_shared_menu_vocabulary',
        sa.Column('kind', sa.String(12), primary_key=True),
        sa.Column('value', sa.String(500), primary_key=True),
        sa.Column('first_revision_id', sa.Integer(), sa.ForeignKey('school_lunch_shared_menu_revisions.id'), nullable=False))
    count_names = ('ingredient_count', 'step_count', 'question_count', 'unanswered_count')
    for name in count_names:
        op.add_column('school_lunch_shared_menu_revisions', sa.Column(name, sa.Integer()))
    revisions = sa.table('school_lunch_shared_menu_revisions', sa.column('id', sa.Integer()),
        sa.column('document', sa.JSON()), sa.column('provenance', sa.JSON()),
        *(sa.column(name, sa.Integer()) for name in count_names))
    cursor = connection.execute(sa.select(revisions.c.id, revisions.c.document, revisions.c.provenance).execution_options(yield_per=1))
    try:
        for row in cursor:
            connection.execute(revisions.update().where(revisions.c.id == row.id).values(
                **revision_counts(row.document, row.provenance)))
            values = vocabulary(row.document, row.id)
            if values:
                connection.execute(sa.text(
                    'INSERT INTO school_lunch_shared_menu_vocabulary (kind, value, first_revision_id) '
                    'VALUES (:kind, :value, :first_revision_id) ON CONFLICT (kind, value) DO NOTHING'
                ), values)
    finally:
        cursor.close()
    with op.batch_alter_table('school_lunch_shared_menu_revisions') as batch:
        for name in count_names:
            batch.alter_column(name, existing_type=sa.Integer(), nullable=False)


def downgrade():
    op.drop_index('ix_users_normalized_account', table_name='users')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('account_lookup')
    op.drop_table('school_lunch_shared_menu_vocabulary')
    with op.batch_alter_table('school_lunch_shared_menu_revisions') as batch:
        for name in ('ingredient_count', 'step_count', 'question_count', 'unanswered_count'):
            batch.drop_column(name)
    with op.batch_alter_table('school_lunch_shared_menus') as batch:
        batch.drop_index('ix_school_lunch_shared_menus_name_identity')
        batch.drop_column('name_identity')
        batch.drop_column('search_name')
