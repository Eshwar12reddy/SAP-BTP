"""The single source of truth for roles, permissions and which scopes they apply to.

Authorization code checks *permissions*, never role names. The database tables
``roles``, ``permissions`` and ``role_permissions`` mirror these definitions (seeded by
migration) for reporting and referential integrity; a test keeps them in sync.
"""

from __future__ import annotations

import enum


class RoleName(enum.StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMINISTRATOR = "administrator"
    # Separately grantable knowledge-base roles. At platform scope they cover GLOBAL
    # knowledge only, never every BTP account.
    KNOWLEDGE_READER = "knowledge_reader"
    KNOWLEDGE_MANAGER = "knowledge_manager"


class ScopeType(enum.StrEnum):
    PLATFORM = "platform"  # the application itself; administration only
    GLOBAL_ACCOUNT = "global_account"
    SUBACCOUNT = "subaccount"
    ENVIRONMENT = "environment"


ACCOUNT_SCOPES = frozenset({ScopeType.GLOBAL_ACCOUNT, ScopeType.SUBACCOUNT})
BTP_SCOPES = ACCOUNT_SCOPES | {ScopeType.ENVIRONMENT}


class Permission(enum.StrEnum):
    # Not tied to a BTP scope: any active grant of a role carrying it is enough.
    CHAT_USE = "chat:use"

    # BTP permissions: require a grant whose scope covers the resource.
    BTP_READ = "btp:read"
    OPERATIONS_REQUEST = "operations:request"
    OPERATIONS_EXECUTE_LOW_RISK = "operations:execute_low_risk"
    APPROVALS_READ = "approvals:read"
    APPROVALS_DECIDE = "approvals:decide"

    # Knowledge base: require a grant whose scope covers the source (platform = global).
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_CREATE = "knowledge:create"
    KNOWLEDGE_UPDATE = "knowledge:update"
    KNOWLEDGE_REMOVE = "knowledge:remove"

    # Administration: platform scope, or delegated to a BTP account subtree.
    USERS_READ = "users:read"
    USERS_CREATE = "users:create"
    USERS_UPDATE = "users:update"
    USERS_DISABLE = "users:disable"
    GRANTS_MANAGE = "grants:manage"
    SESSIONS_REVOKE = "sessions:revoke"
    AUDIT_READ = "audit:read"


PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.CHAT_USE: "Use the chat assistant and view own conversations",
    Permission.BTP_READ: "Read authorized SAP BTP accounts, environments and applications",
    Permission.OPERATIONS_REQUEST: "Request operations on authorized BTP resources",
    Permission.OPERATIONS_EXECUTE_LOW_RISK: "Execute explicitly permitted low-risk operations",
    Permission.APPROVALS_READ: "Read approval requests for authorized resources",
    Permission.APPROVALS_DECIDE: "Approve or reject eligible requests (never own requests)",
    Permission.KNOWLEDGE_READ: "Read knowledge sources and documents in authorized scopes",
    Permission.KNOWLEDGE_CREATE: "Register knowledge sources in authorized scopes",
    Permission.KNOWLEDGE_UPDATE: "Edit knowledge metadata and upload new versions",
    Permission.KNOWLEDGE_REMOVE: "Remove knowledge sources from the active knowledge base",
    Permission.USERS_READ: "View manageable users",
    Permission.USERS_CREATE: "Create user accounts",
    Permission.USERS_UPDATE: "Update user profiles",
    Permission.USERS_DISABLE: "Disable and reactivate user accounts",
    Permission.GRANTS_MANAGE: "Assign and revoke roles and BTP scopes",
    Permission.SESSIONS_REVOKE: "Revoke other users' sessions",
    Permission.AUDIT_READ: "Review authorized audit records",
}

_VIEWER = frozenset({Permission.CHAT_USE, Permission.BTP_READ, Permission.KNOWLEDGE_READ})
KNOWLEDGE_PERMISSIONS = frozenset(
    {
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_CREATE,
        Permission.KNOWLEDGE_UPDATE,
        Permission.KNOWLEDGE_REMOVE,
    }
)

ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.VIEWER: _VIEWER,
    RoleName.OPERATOR: _VIEWER
    | {Permission.OPERATIONS_REQUEST, Permission.OPERATIONS_EXECUTE_LOW_RISK},
    RoleName.APPROVER: _VIEWER | {Permission.APPROVALS_READ, Permission.APPROVALS_DECIDE},
    # Deliberately no BTP or chat permissions: administrators manage access, they do not
    # automatically get to read BTP data.
    RoleName.ADMINISTRATOR: frozenset(
        {
            Permission.USERS_READ,
            Permission.USERS_CREATE,
            Permission.USERS_UPDATE,
            Permission.USERS_DISABLE,
            Permission.GRANTS_MANAGE,
            Permission.SESSIONS_REVOKE,
            Permission.AUDIT_READ,
        }
    ),
    RoleName.KNOWLEDGE_READER: frozenset({Permission.KNOWLEDGE_READ}),
    RoleName.KNOWLEDGE_MANAGER: KNOWLEDGE_PERMISSIONS,
}

ROLE_DESCRIPTIONS: dict[RoleName, str] = {
    RoleName.VIEWER: "Read authorized BTP information and use read-only chat",
    RoleName.OPERATOR: "Viewer, plus request authorized operations",
    RoleName.APPROVER: "Viewer, plus decide on eligible approval requests",
    RoleName.ADMINISTRATOR: "Manage users, roles, scopes and sessions within delegated scope",
    RoleName.KNOWLEDGE_READER: "Read knowledge documents in the granted scope (platform = global)",
    RoleName.KNOWLEDGE_MANAGER: "Register, edit, version and remove knowledge in the granted scope",
}

# Which scope types each role may be granted at.
ROLE_SCOPE_TYPES: dict[RoleName, frozenset[ScopeType]] = {
    RoleName.VIEWER: BTP_SCOPES,
    RoleName.OPERATOR: BTP_SCOPES,
    RoleName.APPROVER: BTP_SCOPES,
    RoleName.ADMINISTRATOR: ACCOUNT_SCOPES | {ScopeType.PLATFORM},
    RoleName.KNOWLEDGE_READER: BTP_SCOPES | {ScopeType.PLATFORM},
    RoleName.KNOWLEDGE_MANAGER: BTP_SCOPES | {ScopeType.PLATFORM},
}

# Administrative actions that a delegated (account-scoped) administrator may not perform.
PLATFORM_ONLY_PERMISSIONS = frozenset(
    {
        Permission.USERS_CREATE,
        Permission.USERS_UPDATE,
        Permission.USERS_DISABLE,
        Permission.SESSIONS_REVOKE,
    }
)
ADMIN_PERMISSIONS = ROLE_PERMISSIONS[RoleName.ADMINISTRATOR]
BTP_PERMISSIONS = frozenset(
    {
        Permission.BTP_READ,
        Permission.OPERATIONS_REQUEST,
        Permission.OPERATIONS_EXECUTE_LOW_RISK,
        Permission.APPROVALS_READ,
        Permission.APPROVALS_DECIDE,
    }
)
