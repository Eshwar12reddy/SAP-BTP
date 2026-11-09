"""Phase 11 BTP connection metadata and active selections.

Secrets remain outside PostgreSQL. This migration adds only opaque references and
server-side selection state.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9f34c1b7a62"
down_revision: str | Sequence[str] | None = "a17b6c8d4e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS = {
    "btp:connections:read": "Read authorized BTP connection metadata",
    "btp:connections:create": "Register authorized BTP connections",
    "btp:connections:update": "Update and disable authorized BTP connections",
    "btp:connections:validate": "Validate authorized BTP connections",
    "btp:environment:select": "Select an authorized active BTP environment",
}


def upgrade() -> None:
    op.create_table(
        "btp_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("btp_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("btp_environment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_endpoint", sa.String(length=2048), nullable=False),
        sa.Column("credential_reference", sa.String(length=255), nullable=False),
        sa.Column("authentication_method", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_validation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validation_result", sa.String(length=40), server_default="not_validated", nullable=False),
        sa.Column("last_validation_error_category", sa.String(length=64), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(btrim(display_name)) > 0", name=op.f("display_name_not_blank")),
        sa.CheckConstraint("char_length(btrim(api_endpoint)) > 0", name=op.f("api_endpoint_not_blank")),
        sa.CheckConstraint("char_length(btrim(credential_reference)) > 0", name=op.f("credential_reference_not_blank")),
        sa.CheckConstraint("char_length(btrim(authentication_method)) > 0", name=op.f("authentication_method_not_blank")),
        sa.CheckConstraint("revision > 0", name=op.f("revision_positive")),
        sa.ForeignKeyConstraint(["btp_account_id"], ["btp_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_environment_id"], ["btp_environments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("btp_environment_id", name="uq_btp_connections_environment"),
    )
    op.create_index("ix_btp_connections_account_id", "btp_connections", ["btp_account_id"])
    op.create_index(
        "ix_btp_connections_environment_enabled",
        "btp_connections",
        ["btp_environment_id", "enabled"],
    )
    op.create_table(
        "btp_active_connections",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["btp_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    permissions = sa.table("permissions", sa.column("key", sa.String), sa.column("description", sa.String))
    op.bulk_insert(permissions, [{"key": key, "description": description} for key, description in _PERMISSIONS.items()])
    for role, keys in {
        "viewer": ["btp:connections:read", "btp:environment:select"],
        "operator": [
            "btp:connections:read",
            "btp:environment:select",
            "btp:connections:create",
            "btp:connections:update",
            "btp:connections:validate",
        ],
        "approver": ["btp:connections:read", "btp:environment:select"],
    }.items():
        op.get_bind().execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
                "WHERE r.name = :role AND p.key = ANY(:keys)"
            ),
            {"role": role, "keys": keys},
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = ANY(:keys))"
        ),
        {"keys": list(_PERMISSIONS)},
    )
    op.get_bind().execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(:keys)"), {"keys": list(_PERMISSIONS)}
    )
    op.drop_table("btp_active_connections")
    op.drop_index("ix_btp_connections_environment_enabled", table_name="btp_connections")
    op.drop_index("ix_btp_connections_account_id", table_name="btp_connections")
    op.drop_table("btp_connections")
