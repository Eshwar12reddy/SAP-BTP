"""Deterministic Phase 22 architecture-advisor contract tests."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.agents.contracts import Route
from app.agents.router import route_by_rules
from app.schemas.architecture import (
    ArchitectureComponent,
    ArchitectureDiagram,
    ArchitectureGenerationRequest,
    ArchitectureProposal,
    ArchitectureRequirements,
    ArchitectureValidation,
)
from app.schemas.recommendations import RecommendationRequest
from app.services.architecture_advisor_service import ArchitectureAdvisorService
from app.services.service_recommendation_service import ServiceRecommendationService


def service() -> ArchitectureAdvisorService:
    return ArchitectureAdvisorService(recommendation_service=ServiceRecommendationService())


def test_architecture_route_is_distinct_from_generic_architecture() -> None:
    assert route_by_rules("Design an SAP BTP architecture for an employee app.") is Route.BTP_ARCHITECTURE
    assert route_by_rules("Suggest an architecture for a document-processing application.") is Route.ARCHITECTURE


def test_requirements_analysis_preserves_unknowns_and_asks_focused_questions() -> None:
    result = service().analyze(
        "Build an employee application connected to SAP S/4HANA for about 1,000 employees "
        "with secure login, approvals and event-driven integration."
    )
    assert result.requirements.user_count == 1_000
    assert result.requirements.existing_sap_systems
    assert result.requirements.assumptions
    assert result.requirements.unknown_requirements
    assert result.analysis_status == "NEEDS_CLARIFICATION"
    assert any("runtime" in item.lower() for item in result.clarification_questions)
    assert any("region" in item.lower() for item in result.missing_critical_information)


@pytest.mark.asyncio
async def test_generated_proposal_is_catalog_backed_and_has_safe_server_graph() -> None:
    proposal = await service().generate(
        request=ArchitectureGenerationRequest(
            request_text=(
                "Build an employee application integrated with SAP S/4HANA, using a database, "
                "secure login, approvals and event-driven integration for about 1,000 employees."
            )
        )
    )
    assert proposal.validation.valid
    assert len({item.component_id for item in proposal.components}) == len(proposal.components)
    assert any(item.related_service_key == "hana-cloud" for item in proposal.components)
    assert any(item.related_service_key == "xsuaa" for item in proposal.components)
    assert any(item.component_id == "sap-s4hana" for item in proposal.components)
    assert proposal.diagram.source == "SERVER_GENERATED"
    assert "<script" not in proposal.diagram.mermaid.lower()
    assert all("$" not in item and "€" not in item for item in proposal.cost_considerations)
    assert all(not re.search(r"(?:[$€]\s*\d|\b\d[\d,.]*\s*(?:usd|eur|dollars?|euros?)\b|\b(?:price|cost)\s*[:=]\s*\d)", item, re.IGNORECASE) for item in proposal.cost_considerations)


@pytest.mark.asyncio
async def test_runtime_preference_selects_runtime_and_keeps_alternatives() -> None:
    for runtime, service_key in (("cloudfoundry", "cloud-foundry-runtime"), ("kyma", "kyma-runtime"), ("abap", "abap-environment")):
        proposal = await service().generate(
            request=ArchitectureGenerationRequest(
                request_text=f"Build a business application on {runtime}.",
            )
        )
        assert any(item.related_service_key == service_key for item in proposal.components)
        assert next(item for item in proposal.runtime_options if item.suitability == "SELECTED").runtime_id == runtime


def test_schema_rejects_duplicate_components_and_unsafe_diagrams() -> None:
    with pytest.raises(ValueError, match="unsafe construct"):
        ArchitectureDiagram(mermaid="flowchart LR\nA-->B\nclick A javascript:alert(1)")
    with pytest.raises(ValidationError, match="component IDs must be unique"):
        ArchitectureProposal(
            proposal_id="00000000-0000-0000-0000-000000000001",
            revision=1,
            title="Invalid",
            overview="Invalid proposal",
            requirements=ArchitectureRequirements(business_objective="test"),
            components=[
                ArchitectureComponent(component_id="duplicate", name="One", component_type="APPLICATION", purpose="one", verification_status="PROPOSED"),
                ArchitectureComponent(component_id="duplicate", name="Two", component_type="APPLICATION", purpose="two", verification_status="PROPOSED"),
            ],
            diagram=ArchitectureDiagram(mermaid="flowchart LR\nA-->B"),
            validation=ArchitectureValidation(valid=False, validated_at="2026-11-05T00:00:00Z"),
            generated_at="2026-11-05T00:00:00Z",
        )


def test_recommendation_request_remains_typed_and_does_not_need_live_calls() -> None:
    request = RecommendationRequest(request_text="Architecture for a secure SAP BTP application")
    assert request.request_text
