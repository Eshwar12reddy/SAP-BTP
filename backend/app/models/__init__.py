"""Importing this package registers every model on ``Base.metadata`` (used by Alembic)."""

from app.models.access_grant import AccessGrant
from app.models.audit_event import AuditEvent, LoginAttempt
from app.models.btp_account import AccountType, BTPAccount, BTPEnvironment, RuntimeType
from app.models.conversation import Conversation
from app.models.knowledge import (
    KnowledgeMediaType,
    KnowledgeScope,
    KnowledgeSource,
    KnowledgeSourceVersion,
    SourceStatus,
    ValidationStatus,
)
from app.models.knowledge_index import (
    IndexStatus,
    KnowledgeChildChunk,
    KnowledgeIndexGeneration,
    KnowledgeParentChunk,
)
from app.models.message import Message, MessageRole
from app.models.role import Permission, Role, RolePermission
from app.models.session import Session, SessionRefreshToken
from app.models.user import User

__all__ = [
    "AccessGrant",
    "AccountType",
    "AuditEvent",
    "BTPAccount",
    "BTPEnvironment",
    "Conversation",
    "KnowledgeMediaType",
    "KnowledgeChildChunk",
    "KnowledgeIndexGeneration",
    "KnowledgeParentChunk",
    "KnowledgeScope",
    "KnowledgeSource",
    "KnowledgeSourceVersion",
    "IndexStatus",
    "LoginAttempt",
    "Message",
    "MessageRole",
    "Permission",
    "Role",
    "RolePermission",
    "RuntimeType",
    "Session",
    "SessionRefreshToken",
    "SourceStatus",
    "User",
    "ValidationStatus",
]
