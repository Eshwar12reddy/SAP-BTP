"""Importing this package registers every model on ``Base.metadata`` (used by Alembic)."""

from app.models.access_grant import AccessGrant
from app.models.agent_approval import AgentApproval, ApprovalDecision, ApprovalStatus
from app.models.architecture_proposal import ArchitectureProposalRecord
from app.models.audit_event import AuditEvent, LoginAttempt
from app.models.btp_account import AccountType, BTPAccount, BTPEnvironment, RuntimeType
from app.models.btp_connection import BTPActiveConnection, BTPConnection, ConnectionValidationResult
from app.models.conversation import Conversation
from app.models.infrastructure_plan import InfrastructurePlanRecord
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
from app.models.memory import Memory, MemoryCategory, MemoryVerification
from app.models.message import Message, MessageRole
from app.models.role import Permission, Role, RolePermission
from app.models.session import Session, SessionRefreshToken
from app.models.sizing_result import SizingResultRecord
from app.models.user import User

__all__ = [
    "AccessGrant",
    "AccountType",
    "AgentApproval",
    "ApprovalDecision",
    "ApprovalStatus",
    "ArchitectureProposalRecord",
    "AuditEvent",
    "BTPAccount",
    "BTPActiveConnection",
    "BTPConnection",
    "BTPEnvironment",
    "ConnectionValidationResult",
    "Conversation",
    "IndexStatus",
    "InfrastructurePlanRecord",
    "KnowledgeChildChunk",
    "KnowledgeIndexGeneration",
    "KnowledgeMediaType",
    "KnowledgeParentChunk",
    "KnowledgeScope",
    "KnowledgeSource",
    "KnowledgeSourceVersion",
    "LoginAttempt",
    "Memory",
    "MemoryCategory",
    "MemoryVerification",
    "Message",
    "MessageRole",
    "Permission",
    "Role",
    "RolePermission",
    "RuntimeType",
    "Session",
    "SessionRefreshToken",
    "SizingResultRecord",
    "SourceStatus",
    "User",
    "ValidationStatus",
]
