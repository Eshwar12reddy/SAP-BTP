"""knowledge sources

Additive: no existing row is updated or deleted.

- New tables ``knowledge_sources`` and ``knowledge_source_versions``. The two reference
  each other: a version's ``source_id`` is DEFERRABLE INITIALLY DEFERRED (registration
  inserts version 1 before its source), and the source's composite active-version key is
  added after both tables exist.
- A trigger makes version rows immutable: no DELETE, and only ``validation_status`` /
  ``validated_at`` may change, only away from ``pending``.
- Seeds four ``knowledge:*`` permissions, the ``knowledge_reader`` and ``knowledge_manager``
  roles, and adds ``knowledge:read`` to viewer, operator and approver (frozen snapshot of
  ``app.auth.permissions``; a test keeps the database equal to the code).
- Replaces two check constraints with wider versions: ``roles.name`` allows the two new
  role names, and ``access_grants`` allows knowledge roles at platform scope (= global
  knowledge only).

Revision ID: cc924a6aa7ae
Revises: 5d8e2a7c41b9
Create Date: 2026-11-01 17:17:47.851208+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cc924a6aa7ae'
down_revision: str | Sequence[str] | None = '5d8e2a7c41b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen snapshot of app.auth.permissions at this revision.
_PERMISSIONS = {
    "knowledge:read": "Read knowledge sources and documents in authorized scopes",
    "knowledge:create": "Register knowledge sources in authorized scopes",
    "knowledge:update": "Edit knowledge metadata and upload new versions",
    "knowledge:remove": "Remove knowledge sources from the active knowledge base",
}
_ROLES = {
    "knowledge_reader": (
        "Read knowledge documents in the granted scope (platform = global)",
        ["knowledge:read"],
    ),
    "knowledge_manager": (
        "Register, edit, version and remove knowledge in the granted scope",
        list(_PERMISSIONS),
    ),
}
_EXISTING_READERS = ["viewer", "operator", "approver"]

_ROLE_NAMES_OLD = "'viewer', 'operator', 'approver', 'administrator'"
_ROLE_NAMES_NEW = f"{_ROLE_NAMES_OLD}, 'knowledge_reader', 'knowledge_manager'"
_ROLE_SCOPE_OLD = (
    "(role_name = 'administrator' AND scope_type <> 'environment') OR "
    "(role_name <> 'administrator' AND scope_type <> 'platform')"
)
_ROLE_SCOPE_NEW = (
    "(role_name = 'administrator' AND scope_type <> 'environment') OR "
    "(role_name IN ('knowledge_reader', 'knowledge_manager')) OR "
    "(role_name NOT IN ('administrator', 'knowledge_reader', 'knowledge_manager') "
    "AND scope_type <> 'platform')"
)

_IMMUTABILITY_FUNCTION = """
CREATE FUNCTION knowledge_versions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'knowledge source versions cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.source_id IS DISTINCT FROM OLD.source_id
        OR NEW.version_number IS DISTINCT FROM OLD.version_number
        OR NEW.original_filename IS DISTINCT FROM OLD.original_filename
        OR NEW.media_type IS DISTINCT FROM OLD.media_type
        OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
        OR NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
        OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
        OR NEW.change_note IS DISTINCT FROM OLD.change_note
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'knowledge source versions are immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.validation_status IS DISTINCT FROM OLD.validation_status
        OR NEW.validated_at IS DISTINCT FROM OLD.validated_at)
        AND OLD.validation_status <> 'pending' THEN
        RAISE EXCEPTION 'validation status can only change from pending'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'knowledge_sources',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=2000), nullable=True),
        sa.Column(
            'tags',
            postgresql.ARRAY(sa.String(length=50)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column('scope_type', sa.String(length=20), nullable=False),
        sa.Column('btp_account_id', sa.UUID(), nullable=True),
        sa.Column('btp_environment_id', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('active_version_id', sa.UUID(), nullable=False),
        sa.Column('active_version_status', sa.String(length=16), nullable=False),
        sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('removed_by', sa.UUID(), nullable=True),
        sa.Column('removal_reason', sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(scope_type = 'global' AND btp_account_id IS NULL AND btp_environment_id IS NULL) "
            "OR (scope_type = 'btp_account' AND btp_account_id IS NOT NULL "
            "AND btp_environment_id IS NULL) "
            "OR (scope_type = 'btp_environment' AND btp_environment_id IS NOT NULL "
            "AND btp_account_id IS NULL)",
            name=op.f('ck_knowledge_sources_scope_shape_valid'),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND removed_at IS NULL AND removed_by IS NULL "
            "AND removal_reason IS NULL) "
            "OR (status = 'removed' AND removed_at IS NOT NULL AND removed_by IS NOT NULL "
            "AND removal_reason IS NOT NULL)",
            name=op.f('ck_knowledge_sources_removal_recorded'),
        ),
        sa.CheckConstraint(
            "active_version_status = 'validated'",
            name=op.f('ck_knowledge_sources_active_version_validated'),
        ),
        sa.CheckConstraint(
            "scope_type IN ('global', 'btp_account', 'btp_environment')",
            name=op.f('ck_knowledge_sources_scope_type_valid'),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed')", name=op.f('ck_knowledge_sources_status_valid')
        ),
        sa.CheckConstraint('cardinality(tags) <= 20', name=op.f('ck_knowledge_sources_tags_bounded')),
        sa.CheckConstraint(
            'char_length(btrim(title)) > 0', name=op.f('ck_knowledge_sources_title_not_blank')
        ),
        sa.CheckConstraint('revision > 0', name=op.f('ck_knowledge_sources_revision_positive')),
        sa.ForeignKeyConstraint(
            ['btp_account_id'],
            ['btp_accounts.id'],
            name=op.f('fk_knowledge_sources_btp_account_id_btp_accounts'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['btp_environment_id'],
            ['btp_environments.id'],
            name=op.f('fk_knowledge_sources_btp_environment_id_btp_environments'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'],
            ['users.id'],
            name=op.f('fk_knowledge_sources_created_by_users'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['owner_id'],
            ['users.id'],
            name=op.f('fk_knowledge_sources_owner_id_users'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['removed_by'],
            ['users.id'],
            name=op.f('fk_knowledge_sources_removed_by_users'),
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_sources')),
    )
    op.create_index('ix_knowledge_sources_btp_account_id', 'knowledge_sources', ['btp_account_id'])
    op.create_index(
        'ix_knowledge_sources_btp_environment_id', 'knowledge_sources', ['btp_environment_id']
    )
    op.create_index('ix_knowledge_sources_owner_id', 'knowledge_sources', ['owner_id'])
    op.create_index(
        'ix_knowledge_sources_status_created_at', 'knowledge_sources', ['status', 'created_at']
    )
    op.create_index(
        'ix_knowledge_sources_tags', 'knowledge_sources', ['tags'], postgresql_using='gin'
    )

    op.create_table(
        'knowledge_source_versions',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('media_type', sa.String(length=100), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('storage_key', sa.String(length=32), nullable=False),
        sa.Column('uploaded_by', sa.UUID(), nullable=False),
        sa.Column(
            'validation_status', sa.String(length=16), server_default='pending', nullable=False
        ),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('change_note', sa.String(length=500), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.CheckConstraint(
            "(validation_status = 'pending') = (validated_at IS NULL)",
            name=op.f('ck_knowledge_source_versions_validation_timestamp'),
        ),
        sa.CheckConstraint(
            "media_type IN ('application/pdf', 'text/plain', 'text/markdown')",
            name=op.f('ck_knowledge_source_versions_media_type_valid'),
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_knowledge_source_versions_sha256_format')
        ),
        sa.CheckConstraint(
            "storage_key ~ '^[0-9a-f]{32}$'",
            name=op.f('ck_knowledge_source_versions_storage_key_format'),
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'validated', 'rejected')",
            name=op.f('ck_knowledge_source_versions_validation_status_valid'),
        ),
        sa.CheckConstraint(
            'char_length(btrim(original_filename)) > 0',
            name=op.f('ck_knowledge_source_versions_filename_not_blank'),
        ),
        sa.CheckConstraint(
            'size_bytes > 0', name=op.f('ck_knowledge_source_versions_size_positive')
        ),
        sa.CheckConstraint(
            'version_number > 0', name=op.f('ck_knowledge_source_versions_version_number_positive')
        ),
        sa.ForeignKeyConstraint(
            ['source_id'],
            ['knowledge_sources.id'],
            name=op.f('fk_knowledge_source_versions_source_id_knowledge_sources'),
            ondelete='RESTRICT',
            initially='DEFERRED',
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ['uploaded_by'],
            ['users.id'],
            name=op.f('fk_knowledge_source_versions_uploaded_by_users'),
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_source_versions')),
        sa.UniqueConstraint(
            'id', 'source_id', 'validation_status', name='uq_knowledge_versions_id_source_status'
        ),
        sa.UniqueConstraint(
            'source_id', 'version_number', name='uq_knowledge_versions_source_number'
        ),
        sa.UniqueConstraint('storage_key', name=op.f('uq_knowledge_source_versions_storage_key')),
    )
    # Added after both tables exist (they reference each other).
    op.create_foreign_key(
        'fk_knowledge_sources_active_version',
        'knowledge_sources',
        'knowledge_source_versions',
        ['active_version_id', 'id', 'active_version_status'],
        ['id', 'source_id', 'validation_status'],
        ondelete='RESTRICT',
    )
    op.execute(_IMMUTABILITY_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_knowledge_versions_immutable "
        "BEFORE UPDATE OR DELETE ON knowledge_source_versions "
        "FOR EACH ROW EXECUTE FUNCTION knowledge_versions_immutable()"
    )

    # Roles and permissions: widen the checks first, then seed.
    op.drop_constraint(op.f('ck_roles_name_known'), 'roles', type_='check')
    op.create_check_constraint(op.f('ck_roles_name_known'), 'roles', f"name IN ({_ROLE_NAMES_NEW})")
    op.drop_constraint(
        op.f('ck_access_grants_role_scope_compatible'), 'access_grants', type_='check'
    )
    op.create_check_constraint(
        op.f('ck_access_grants_role_scope_compatible'), 'access_grants', _ROLE_SCOPE_NEW
    )
    _seed()


def _seed() -> None:
    permissions = sa.table(
        "permissions", sa.column("key", sa.String), sa.column("description", sa.String)
    )
    roles = sa.table("roles", sa.column("name", sa.String), sa.column("description", sa.String))
    op.bulk_insert(permissions, [{"key": k, "description": d} for k, d in _PERMISSIONS.items()])
    op.bulk_insert(roles, [{"name": n, "description": d} for n, (d, _) in _ROLES.items()])
    grants = [(name, keys) for name, (_, keys) in _ROLES.items()]
    grants += [(name, ["knowledge:read"]) for name in _EXISTING_READERS]
    for name, keys in grants:
        op.get_bind().execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.key = ANY(:keys) "
                "WHERE r.name = :name"
            ),
            {"name": name, "keys": keys},
        )


def downgrade() -> None:
    """Downgrade schema. Destroys all knowledge metadata and knowledge role grants (stored
    files are not touched); never run automatically."""
    op.execute("DROP TRIGGER trg_knowledge_versions_immutable ON knowledge_source_versions")
    op.execute("DROP FUNCTION knowledge_versions_immutable()")
    op.drop_constraint('fk_knowledge_sources_active_version', 'knowledge_sources', type_='foreignkey')
    op.drop_table('knowledge_source_versions')
    op.drop_index('ix_knowledge_sources_tags', table_name='knowledge_sources')
    op.drop_index('ix_knowledge_sources_status_created_at', table_name='knowledge_sources')
    op.drop_index('ix_knowledge_sources_owner_id', table_name='knowledge_sources')
    op.drop_index('ix_knowledge_sources_btp_environment_id', table_name='knowledge_sources')
    op.drop_index('ix_knowledge_sources_btp_account_id', table_name='knowledge_sources')
    op.drop_table('knowledge_sources')

    bind = op.get_bind()
    role_names = list(_ROLES)
    bind.execute(
        sa.text("DELETE FROM access_grants WHERE role_name = ANY(:names)"), {"names": role_names}
    )
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = ANY(:keys))"
        ),
        {"keys": list(_PERMISSIONS)},
    )
    bind.execute(sa.text("DELETE FROM roles WHERE name = ANY(:names)"), {"names": role_names})
    bind.execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(:keys)"), {"keys": list(_PERMISSIONS)}
    )
    op.drop_constraint(
        op.f('ck_access_grants_role_scope_compatible'), 'access_grants', type_='check'
    )
    op.create_check_constraint(
        op.f('ck_access_grants_role_scope_compatible'), 'access_grants', _ROLE_SCOPE_OLD
    )
    op.drop_constraint(op.f('ck_roles_name_known'), 'roles', type_='check')
    op.create_check_constraint(op.f('ck_roles_name_known'), 'roles', f"name IN ({_ROLE_NAMES_OLD})")
