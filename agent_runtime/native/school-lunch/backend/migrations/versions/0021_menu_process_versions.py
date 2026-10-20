"""Versioned editable menu processes and non-destructive Gemini proposals."""
from alembic import op
import sqlalchemy as sa

revision = '0021_menu_process_versions'
down_revision = '0020_schedule_explicit_inputs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('school_lunch_process_recipes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('facility_id', sa.Integer(), sa.ForeignKey('school_lunch_facilities.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('head_revision_id', sa.Integer()), sa.Column('confirmed_revision_id', sa.Integer()))
    op.create_index('ix_school_lunch_process_recipes_facility_id', 'school_lunch_process_recipes', ['facility_id'])
    op.create_table('school_lunch_process_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('recipe_id', sa.Integer(), sa.ForeignKey('school_lunch_process_recipes.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('parent_revision_id', sa.Integer(), sa.ForeignKey('school_lunch_process_revisions.id')),
        sa.Column('menu_execution_id', sa.Integer(), sa.ForeignKey('school_lunch_menu_executions.id'), nullable=False),
        sa.Column('scope', sa.String(10), nullable=False), sa.Column('status', sa.String(12), nullable=False),
        sa.Column('origin', sa.String(30), nullable=False), sa.Column('actor_role', sa.String(30), nullable=False),
        sa.Column('document', sa.JSON(), nullable=False), sa.Column('source_snapshot', sa.JSON(), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=False), sa.Column('change_note', sa.Text(), nullable=False),
        sa.Column('user_constraints', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('recipe_id', 'number', name='uq_process_revision_number'))
    op.create_index('ix_school_lunch_process_revisions_recipe_id', 'school_lunch_process_revisions', ['recipe_id'])
    op.create_table('school_lunch_process_bindings',
        sa.Column('menu_execution_id', sa.Integer(), sa.ForeignKey('school_lunch_menu_executions.id'), primary_key=True),
        sa.Column('recipe_id', sa.Integer(), sa.ForeignKey('school_lunch_process_recipes.id'), nullable=False),
        sa.Column('revision_id', sa.Integer(), sa.ForeignKey('school_lunch_process_revisions.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False))
    op.create_table('school_lunch_process_proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('menu_execution_id', sa.Integer(), sa.ForeignKey('school_lunch_menu_executions.id'), nullable=False),
        sa.Column('base_version', sa.Integer(), nullable=False), sa.Column('model', sa.String(200), nullable=False),
        sa.Column('prompt_hash', sa.String(64), nullable=False), sa.Column('example_revision_ids', sa.JSON(), nullable=False),
        sa.Column('input_document', sa.JSON(), nullable=False), sa.Column('document', sa.JSON(), nullable=False),
        sa.Column('inference_trace', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_school_lunch_process_proposals_menu_execution_id', 'school_lunch_process_proposals', ['menu_execution_id'])


def downgrade():
    for table in ('school_lunch_process_revisions', 'school_lunch_process_proposals'):
        if op.get_bind().execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first():
            raise RuntimeError('保存された工程履歴・提案があるため削除できません')
    for table in ('school_lunch_process_proposals', 'school_lunch_process_bindings',
                  'school_lunch_process_revisions', 'school_lunch_process_recipes'):
        op.drop_table(table)
