"""Add strict facility template lineage constraints."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_STATUS_VALUES = ("draft", "active", "archived", "invalid", "repair_blocked")


def _index_names(table_name: str) -> set[str]:
    return {str(index.get("name") or "") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _check_names(table_name: str) -> set[str]:
    return {str(check.get("name") or "") for check in sa.inspect(op.get_bind()).get_check_constraints(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    indexes = _index_names("facility_template_versions")
    if "uq_facility_template_versions_active_facility" not in indexes:
        bind.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_facility_template_versions_active_facility
                ON facility_template_versions(facility_id)
                WHERE status = 'active'
                """
            )
        )
    if "uq_facility_template_versions_facility_digest" not in indexes:
        op.create_index(
            "uq_facility_template_versions_facility_digest",
            "facility_template_versions",
            ["facility_id", "template_digest"],
            unique=True,
        )
    if bind.dialect.name != "sqlite" and "ck_facility_template_versions_status" not in _check_names(
        "facility_template_versions"
    ):
        op.create_check_constraint(
            "ck_facility_template_versions_status",
            "facility_template_versions",
            f"status IN {tuple(_STATUS_VALUES)!r}",
        )


def downgrade() -> None:
    indexes = _index_names("facility_template_versions")
    if "uq_facility_template_versions_facility_digest" in indexes:
        op.drop_index("uq_facility_template_versions_facility_digest", table_name="facility_template_versions")
    if "uq_facility_template_versions_active_facility" in indexes:
        op.drop_index("uq_facility_template_versions_active_facility", table_name="facility_template_versions")
    if op.get_bind().dialect.name != "sqlite" and "ck_facility_template_versions_status" in _check_names(
        "facility_template_versions"
    ):
        op.drop_constraint(
            "ck_facility_template_versions_status",
            "facility_template_versions",
            type_="check",
        )
