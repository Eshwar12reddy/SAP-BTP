"""authentication rbac and btp scopes

Additive only: new tables for users, roles, permissions, BTP account mapping, access
grants, sessions, refresh tokens and audit events, plus three NULLable columns on
``conversations``. Existing conversations keep ``owner_id = NULL`` (legacy) and stay
inaccessible until ownership is assigned explicitly. Roles and permissions are seeded
from a frozen snapshot; a test keeps it equal to ``app.auth.permissions``.

Revision ID: 360b3bd3d310
Revises: bf219527df9c
Create Date: 2026-11-01 14:57:34.526075+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '360b3bd3d310'
down_revision: str | Sequence[str] | None = 'bf219527df9c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('auth_login_attempts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('email_hash', sa.String(length=64), nullable=False),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('succeeded', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_login_attempts'))
    )
    op.create_index('ix_auth_login_attempts_email_hash_created_at', 'auth_login_attempts', ['email_hash', 'created_at'], unique=False)
    op.create_index('ix_auth_login_attempts_ip_address_created_at', 'auth_login_attempts', ['ip_address', 'created_at'], unique=False)
    op.create_table('btp_accounts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('external_account_id', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('account_type', sa.String(length=50), nullable=False),
    sa.Column('region', sa.String(length=50), nullable=True),
    sa.Column('parent_account_id', sa.UUID(), nullable=True),
    sa.Column('parent_account_type', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(account_type = 'global_account' AND parent_account_id IS NULL AND parent_account_type IS NULL) OR (account_type = 'subaccount' AND parent_account_id IS NOT NULL AND parent_account_type = 'global_account')", name=op.f('ck_btp_accounts_hierarchy_valid')),
    sa.CheckConstraint("account_type IN ('global_account', 'subaccount')", name=op.f('ck_btp_accounts_account_type_valid')),
    sa.ForeignKeyConstraint(['parent_account_id', 'parent_account_type'], ['btp_accounts.id', 'btp_accounts.account_type'], name='fk_btp_accounts_parent_global_account', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_btp_accounts')),
    sa.UniqueConstraint('external_account_id', name=op.f('uq_btp_accounts_external_account_id')),
    sa.UniqueConstraint('id', 'account_type', name='uq_btp_accounts_id_account_type')
    )
    op.create_index('ix_btp_accounts_parent_account_id', 'btp_accounts', ['parent_account_id'], unique=False)
    op.create_table('permissions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    sa.UniqueConstraint('key', name=op.f('uq_permissions_key'))
    )
    op.create_table('roles',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("name IN ('viewer', 'operator', 'approver', 'administrator')", name=op.f('ck_roles_name_known')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    sa.UniqueConstraint('id', 'name', name='uq_roles_id_name'),
    sa.UniqueConstraint('name', name=op.f('uq_roles_name'))
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.String(length=150), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(display_name)) > 0', name=op.f('ck_users_display_name_not_blank')),
    sa.CheckConstraint('char_length(email) >= 3', name=op.f('ck_users_email_not_empty')),
    sa.CheckConstraint('email = lower(btrim(email))', name=op.f('ck_users_email_normalized')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('btp_environments',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('btp_account_id', sa.UUID(), nullable=False),
    sa.Column('btp_account_type', sa.String(length=50), server_default='subaccount', nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('runtime_type', sa.String(length=100), nullable=False),
    sa.Column('external_runtime_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btp_account_type = 'subaccount'", name=op.f('ck_btp_environments_in_subaccount')),
    sa.CheckConstraint("runtime_type IN ('cloudfoundry', 'kyma', 'abap', 'neo')", name=op.f('ck_btp_environments_runtime_type_valid')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_btp_environments_name_not_blank')),
    sa.ForeignKeyConstraint(['btp_account_id', 'btp_account_type'], ['btp_accounts.id', 'btp_accounts.account_type'], name='fk_btp_environments_subaccount', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_btp_environments')),
    sa.UniqueConstraint('btp_account_id', 'name', name='uq_btp_environment_account_name')
    )
    op.create_index('ix_btp_environments_btp_account_id', 'btp_environments', ['btp_account_id'], unique=False)
    op.create_index('uq_btp_environments_runtime_external_id', 'btp_environments', ['runtime_type', 'external_runtime_id'], unique=True, postgresql_where=sa.text('external_runtime_id IS NOT NULL'))
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_role_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permissions'))
    )
    op.create_table('sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('csrf_token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('idle_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('absolute_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=50), nullable=True),
    sa.CheckConstraint('char_length(csrf_token_hash) = 64', name=op.f('ck_sessions_csrf_hash_length')),
    sa.CheckConstraint('idle_expires_at <= absolute_expires_at', name=op.f('ck_sessions_idle_within_absolute')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions'))
    )
    op.create_index('ix_sessions_user_id_unrevoked', 'sessions', ['user_id'], unique=False, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('access_grants',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('role_name', sa.String(length=50), nullable=False),
    sa.Column('scope_type', sa.String(length=30), nullable=False),
    sa.Column('btp_account_id', sa.UUID(), nullable=True),
    sa.Column('btp_environment_id', sa.UUID(), nullable=True),
    sa.Column('include_descendants', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('granted_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.UUID(), nullable=True),
    sa.CheckConstraint("(role_name = 'administrator' AND scope_type <> 'environment') OR (role_name <> 'administrator' AND scope_type <> 'platform')", name=op.f('ck_access_grants_role_scope_compatible')),
    sa.CheckConstraint("(scope_type = 'platform' AND btp_account_id IS NULL AND btp_environment_id IS NULL AND NOT include_descendants) OR (scope_type IN ('global_account', 'subaccount') AND btp_account_id IS NOT NULL AND btp_environment_id IS NULL) OR (scope_type = 'environment' AND btp_environment_id IS NOT NULL AND btp_account_id IS NULL AND NOT include_descendants)", name=op.f('ck_access_grants_scope_shape_valid')),
    sa.CheckConstraint("scope_type IN ('platform', 'global_account', 'subaccount', 'environment')", name=op.f('ck_access_grants_scope_type_valid')),
    sa.CheckConstraint('expires_at IS NULL OR expires_at > created_at', name=op.f('ck_access_grants_expiry_valid')),
    sa.ForeignKeyConstraint(['btp_account_id', 'scope_type'], ['btp_accounts.id', 'btp_accounts.account_type'], name='fk_access_grants_btp_account', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['btp_environment_id'], ['btp_environments.id'], name=op.f('fk_access_grants_btp_environment_id_btp_environments'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], name=op.f('fk_access_grants_granted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_access_grants_revoked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id', 'role_name'], ['roles.id', 'roles.name'], name='fk_access_grants_role', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_access_grants_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_grants'))
    )
    op.create_index('ix_access_grants_btp_account_id', 'access_grants', ['btp_account_id'], unique=False)
    op.create_index('ix_access_grants_btp_environment_id', 'access_grants', ['btp_environment_id'], unique=False)
    op.create_index('ix_access_grants_user_id_unrevoked', 'access_grants', ['user_id'], unique=False, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('uq_access_grants_active', 'access_grants', ['user_id', 'role_id', 'scope_type', sa.literal_column("COALESCE(btp_account_id, btp_environment_id, '00000000-0000-0000-0000-000000000000'::uuid)")], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('audit_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('target_user_id', sa.UUID(), nullable=True),
    sa.Column('scope_type', sa.String(length=30), nullable=True),
    sa.Column('btp_account_id', sa.UUID(), nullable=True),
    sa.Column('btp_environment_id', sa.UUID(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.CheckConstraint("outcome IN ('success', 'failure', 'denied')", name=op.f('ck_audit_events_outcome_valid')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_events_actor_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['btp_account_id'], ['btp_accounts.id'], name=op.f('fk_audit_events_btp_account_id_btp_accounts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['btp_environment_id'], ['btp_environments.id'], name=op.f('fk_audit_events_btp_environment_id_btp_environments'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_audit_events_target_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index('ix_audit_events_actor_user_id', 'audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_audit_events_btp_account_id', 'audit_events', ['btp_account_id'], unique=False)
    op.create_index('ix_audit_events_btp_environment_id', 'audit_events', ['btp_environment_id'], unique=False)
    op.create_index('ix_audit_events_created_at', 'audit_events', ['created_at'], unique=False)
    op.create_index('ix_audit_events_event_type_created_at', 'audit_events', ['event_type', 'created_at'], unique=False)
    op.create_index('ix_audit_events_target_user_id', 'audit_events', ['target_user_id'], unique=False)
    op.create_table('session_refresh_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_token_id', sa.UUID(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(token_hash) = 64', name=op.f('ck_session_refresh_tokens_token_hash_length')),
    sa.ForeignKeyConstraint(['replaced_by_token_id'], ['session_refresh_tokens.id'], name=op.f('fk_session_refresh_tokens_replaced_by_token_id_session_refresh_tokens'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], name=op.f('fk_session_refresh_tokens_session_id_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_session_refresh_tokens')),
    sa.UniqueConstraint('replaced_by_token_id', name=op.f('uq_session_refresh_tokens_replaced_by_token_id')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_session_refresh_tokens_token_hash'))
    )
    op.create_index('ix_session_refresh_tokens_session_id', 'session_refresh_tokens', ['session_id'], unique=False)
    op.add_column('conversations', sa.Column('owner_id', sa.UUID(), nullable=True))
    op.add_column('conversations', sa.Column('btp_account_id', sa.UUID(), nullable=True))
    op.add_column('conversations', sa.Column('btp_environment_id', sa.UUID(), nullable=True))
    op.create_index('ix_conversations_owner_id_updated_at_id', 'conversations', ['owner_id', 'updated_at', 'id'], unique=False)
    op.create_foreign_key(op.f('fk_conversations_btp_environment_id_btp_environments'), 'conversations', 'btp_environments', ['btp_environment_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_conversations_owner_id_users'), 'conversations', 'users', ['owner_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_conversations_btp_account_id_btp_accounts'), 'conversations', 'btp_accounts', ['btp_account_id'], ['id'], ondelete='RESTRICT')
    # Existing rows have both columns NULL, so this cannot fail on legacy data.
    op.create_check_constraint(op.f('ck_conversations_single_btp_context'), 'conversations', 'btp_account_id IS NULL OR btp_environment_id IS NULL')
    _seed_roles_and_permissions()


# Frozen snapshot of app.auth.permissions at this revision.
_PERMISSIONS = {
    "chat:use": "Use the chat assistant and view own conversations",
    "btp:read": "Read authorized SAP BTP accounts, environments and applications",
    "operations:request": "Request operations on authorized BTP resources",
    "operations:execute_low_risk": "Execute explicitly permitted low-risk operations",
    "approvals:read": "Read approval requests for authorized resources",
    "approvals:decide": "Approve or reject eligible requests (never own requests)",
    "users:read": "View manageable users",
    "users:create": "Create user accounts",
    "users:update": "Update user profiles",
    "users:disable": "Disable and reactivate user accounts",
    "grants:manage": "Assign and revoke roles and BTP scopes",
    "sessions:revoke": "Revoke other users' sessions",
    "audit:read": "Review authorized audit records",
}
_VIEWER = ["chat:use", "btp:read"]
_ROLES = {
    "viewer": ("Read authorized BTP information and use read-only chat", _VIEWER),
    "operator": (
        "Viewer, plus request authorized operations",
        _VIEWER + ["operations:request", "operations:execute_low_risk"],
    ),
    "approver": (
        "Viewer, plus decide on eligible approval requests",
        _VIEWER + ["approvals:read", "approvals:decide"],
    ),
    "administrator": (
        "Manage users, roles, scopes and sessions within delegated scope",
        ["users:read", "users:create", "users:update", "users:disable",
         "grants:manage", "sessions:revoke", "audit:read"],
    ),
}


def _seed_roles_and_permissions() -> None:
    permissions = sa.table("permissions", sa.column("key", sa.String), sa.column("description", sa.String))
    roles = sa.table("roles", sa.column("name", sa.String), sa.column("description", sa.String))
    op.bulk_insert(permissions, [{"key": k, "description": d} for k, d in _PERMISSIONS.items()])
    op.bulk_insert(roles, [{"name": n, "description": d} for n, (d, _) in _ROLES.items()])
    for name, (_, keys) in _ROLES.items():
        op.get_bind().execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.key = ANY(:keys) "
                "WHERE r.name = :name"
            ),
            {"name": name, "keys": keys},
        )


def downgrade() -> None:
    """Downgrade schema. Destroys all Phase 2 data; never run automatically."""
    op.drop_constraint(op.f('ck_conversations_single_btp_context'), 'conversations', type_='check')
    op.drop_constraint(op.f('fk_conversations_btp_account_id_btp_accounts'), 'conversations', type_='foreignkey')
    op.drop_constraint(op.f('fk_conversations_owner_id_users'), 'conversations', type_='foreignkey')
    op.drop_constraint(op.f('fk_conversations_btp_environment_id_btp_environments'), 'conversations', type_='foreignkey')
    op.drop_index('ix_conversations_owner_id_updated_at_id', table_name='conversations')
    op.drop_column('conversations', 'btp_environment_id')
    op.drop_column('conversations', 'btp_account_id')
    op.drop_column('conversations', 'owner_id')
    op.drop_index('ix_session_refresh_tokens_session_id', table_name='session_refresh_tokens')
    op.drop_table('session_refresh_tokens')
    op.drop_index('ix_audit_events_target_user_id', table_name='audit_events')
    op.drop_index('ix_audit_events_event_type_created_at', table_name='audit_events')
    op.drop_index('ix_audit_events_created_at', table_name='audit_events')
    op.drop_index('ix_audit_events_btp_environment_id', table_name='audit_events')
    op.drop_index('ix_audit_events_btp_account_id', table_name='audit_events')
    op.drop_index('ix_audit_events_actor_user_id', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('uq_access_grants_active', table_name='access_grants', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_access_grants_user_id_unrevoked', table_name='access_grants', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_access_grants_btp_environment_id', table_name='access_grants')
    op.drop_index('ix_access_grants_btp_account_id', table_name='access_grants')
    op.drop_table('access_grants')
    op.drop_index('ix_sessions_user_id_unrevoked', table_name='sessions', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('sessions')
    op.drop_table('role_permissions')
    op.drop_index('uq_btp_environments_runtime_external_id', table_name='btp_environments', postgresql_where=sa.text('external_runtime_id IS NOT NULL'))
    op.drop_index('ix_btp_environments_btp_account_id', table_name='btp_environments')
    op.drop_table('btp_environments')
    op.drop_table('users')
    op.drop_table('roles')
    op.drop_table('permissions')
    op.drop_index('ix_btp_accounts_parent_account_id', table_name='btp_accounts')
    op.drop_table('btp_accounts')
    op.drop_index('ix_auth_login_attempts_ip_address_created_at', table_name='auth_login_attempts')
    op.drop_index('ix_auth_login_attempts_email_hash_created_at', table_name='auth_login_attempts')
    op.drop_table('auth_login_attempts')
