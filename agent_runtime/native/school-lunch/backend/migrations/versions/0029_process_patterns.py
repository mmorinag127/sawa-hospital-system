"""School process-pattern pool and immutable daily application provenance."""
from alembic import op
import sqlalchemy as sa

revision = '0029_process_patterns'
down_revision = '0028_ocr_source_execution'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('school_lunch_process_patterns', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('facility_id', sa.Integer(), sa.ForeignKey('school_lunch_facilities.id'), nullable=False), sa.Column('name', sa.String(200), nullable=False), sa.Column('version', sa.Integer(), nullable=False), sa.Column('head_revision_id', sa.Integer(), nullable=True))
    op.create_index('ix_school_lunch_process_patterns_facility_id', 'school_lunch_process_patterns', ['facility_id'])
    op.create_table('school_lunch_process_pattern_revisions', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('pattern_id', sa.Integer(), sa.ForeignKey('school_lunch_process_patterns.id'), nullable=False), sa.Column('number', sa.Integer(), nullable=False), sa.Column('parent_revision_id', sa.Integer(), sa.ForeignKey('school_lunch_process_pattern_revisions.id'), nullable=True), sa.Column('material_slots', sa.JSON(), nullable=False), sa.Column('document', sa.JSON(), nullable=False), sa.Column('fixed_equipment', sa.JSON(), nullable=False), sa.Column('execution_choices', sa.JSON(), nullable=False), sa.Column('change_note', sa.Text(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('pattern_id', 'number', name='uq_process_pattern_revision_number'))
    op.create_index('ix_school_lunch_process_pattern_revisions_pattern_id', 'school_lunch_process_pattern_revisions', ['pattern_id'])
    op.create_table('school_lunch_process_pattern_applications', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('process_revision_id', sa.Integer(), sa.ForeignKey('school_lunch_process_revisions.id'), nullable=False), sa.Column('pattern_revision_id', sa.Integer(), sa.ForeignKey('school_lunch_process_pattern_revisions.id'), nullable=False), sa.Column('application_id', sa.String(80), nullable=False), sa.Column('material_mapping', sa.JSON(), nullable=False), sa.Column('execution_selections', sa.JSON(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('process_revision_id', 'application_id', name='uq_process_pattern_application_revision'))
    op.create_index('ix_process_pattern_application_revision', 'school_lunch_process_pattern_applications', ['process_revision_id'])
    op.add_column('school_lunch_daily_work_schedules', sa.Column('source_process_revisions', sa.JSON(), nullable=True))

def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM school_lunch_process_patterns LIMIT 1')).first():
        raise RuntimeError('保存済み工程パターンまたは採用履歴があるため削除できません')
    op.drop_column('school_lunch_daily_work_schedules', 'source_process_revisions')
    op.drop_table('school_lunch_process_pattern_applications')
    op.drop_table('school_lunch_process_pattern_revisions')
    op.drop_table('school_lunch_process_patterns')
