"""Immutable facility drawing profiles and generated process-route artifacts."""

from alembic import op
import sqlalchemy as sa

revision = "0022_process_route_profiles"
down_revision = "0021_menu_process_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_lunch_process_route_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "facility_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_facilities.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("head_revision_id", sa.Integer()),
        sa.Column("confirmed_revision_id", sa.Integer()),
    )
    op.create_index(
        "ix_school_lunch_process_route_profiles_facility_id",
        "school_lunch_process_route_profiles",
        ["facility_id"],
    )
    op.create_table(
        "school_lunch_process_route_profile_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_process_route_profiles.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_revision_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_process_route_profile_revisions.id"),
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("template_id", sa.String(80), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("svg_snapshot", sa.Text(), nullable=False),
        sa.Column("manifest_snapshot", sa.JSON(), nullable=False),
        sa.Column("network_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hashes", sa.JSON(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "profile_id", "number", name="uq_process_route_profile_revision_number"
        ),
    )
    op.create_index(
        "ix_school_lunch_process_route_profile_revisions_profile_id",
        "school_lunch_process_route_profile_revisions",
        ["profile_id"],
    )
    op.create_table(
        "school_lunch_process_route_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "menu_execution_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_menu_executions.id"),
            nullable=False,
        ),
        sa.Column(
            "process_revision_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_process_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "profile_revision_id",
            sa.Integer(),
            sa.ForeignKey("school_lunch_process_route_profile_revisions.id"),
            nullable=False,
        ),
        sa.Column("renderer_version", sa.String(120), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("svg", sa.Text(), nullable=False),
        sa.Column("svg_hash", sa.String(64), nullable=False),
        sa.Column("topology", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("generation_status", sa.String(20), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_school_lunch_process_route_artifacts_menu_execution_id",
        "school_lunch_process_route_artifacts",
        ["menu_execution_id"],
    )


def downgrade():
    for table in (
        "school_lunch_process_route_profiles",
        "school_lunch_process_route_profile_revisions",
        "school_lunch_process_route_artifacts",
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(
                "保存済みの動線プロファイル・成果物があるため削除できません"
            )
    op.drop_table("school_lunch_process_route_artifacts")
    op.drop_table("school_lunch_process_route_profile_revisions")
    op.drop_table("school_lunch_process_route_profiles")
