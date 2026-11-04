"""Read-only, SQL-authorized knowledge search tool for the existing frozen registry."""

from __future__ import annotations

from pydantic import Field

from app.agents.contracts import AgentContext
from app.auth.permissions import Permission
from app.schemas.retrieval import KnowledgeEvidence
from app.services.retrieval_service import search_knowledge
from app.tools.contracts import OperationType, SourceType, Tool, ToolInput, ToolOutput
from app.tools.registry import ToolRegistry


class KnowledgeSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=1000)
    max_results: int = Field(default=6, ge=1, le=8)


class KnowledgeSearchOutput(ToolOutput):
    query: str
    retrieval_mode: str
    indexing_status: str
    semantic_status: str
    reranking_status: str
    answer_mode: str
    items: list[KnowledgeEvidence]


async def _knowledge_search(arguments: KnowledgeSearchInput, context: AgentContext) -> KnowledgeSearchOutput:
    settings = context.settings
    if settings is None:
        raise RuntimeError("knowledge search settings unavailable")
    result = await search_knowledge(
        context.session,
        context.principal,
        settings,
        arguments.query,
        max_results=arguments.max_results,
        embedding_client=context.embedding_client,
        redactor=context.redactor,
    )
    return KnowledgeSearchOutput(**result.model_dump(mode="json"))


KNOWLEDGE_SEARCH_TOOL = Tool(
    name="knowledge_search",
    description=(
        "Search only the current user's authorized, published knowledge document chunks. "
        "Returns exact excerpts and backend-verified citations."
    ),
    input_model=KnowledgeSearchInput,
    output_model=KnowledgeSearchOutput,
    required_permission=Permission.KNOWLEDGE_READ,
    operation_type=OperationType.READ,
    source_type=SourceType.KNOWLEDGE_DOCUMENTS,
    run=_knowledge_search,
    timeout_seconds=15.0,
)


def register_knowledge_tool(registry: ToolRegistry) -> None:
    registry.register(KNOWLEDGE_SEARCH_TOOL)
