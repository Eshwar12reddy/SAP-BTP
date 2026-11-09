"""Static Phase 24 generation and safety contracts."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic import ValidationError

from app.agents.contracts import Route
from app.agents.router import route_by_rules
from app.schemas.architecture import ArchitectureGenerationRequest
from app.schemas.infrastructure_plan import (
    InfrastructureApplicationInput,
    InfrastructureMtaModule,
    InfrastructurePlanRequirements,
    InfrastructureTerraformSubaccount,
)
from app.schemas.sizing import SizingRequirements
from app.services.architecture_advisor_service import ArchitectureAdvisorService
from app.services.infrastructure_plan_service import InfrastructurePlanService
from app.services.service_recommendation_service import ServiceRecommendationService
from app.services.sizing_service import SizingService


@pytest.fixture
def proposal():
    service = ArchitectureAdvisorService(recommendation_service=ServiceRecommendationService())
    return asyncio.run(service.generate(request=ArchitectureGenerationRequest(request_text="Build a Cloud Foundry employee application.")))


def test_phase24_route_is_dedicated() -> None:
    assert route_by_rules("Generate a Cloud Foundry manifest for my architecture") is Route.BTP_INFRASTRUCTURE_PLAN
    assert route_by_rules("Create an MTA descriptor for this SAP BTP architecture") is Route.BTP_INFRASTRUCTURE_PLAN


def test_cloud_foundry_manifest_is_deterministic_and_uses_sizing(proposal) -> None:
    generator = InfrastructurePlanService()
    sizing = SizingService().calculate_sync(SizingRequirements(instance_count=2, configured_memory_per_instance=512, configured_disk_per_instance=256))
    request = InfrastructurePlanRequirements(architecture_proposal_id=proposal.proposal_id, output_format="cloudfoundry", application_name="employee-api", applications=[InfrastructureApplicationInput(name="employee-api", environment_variables=["DATABASE_URL"])])
    first = generator._render_cf(request, proposal, sizing, [], [], [])
    second = generator._render_cf(request, proposal, sizing, [], [], [])
    assert first[3][0].content == second[3][0].content
    assert "((DATABASE_URL))" in first[3][0].content
    assert "1024MB" in first[3][0].content
    assert "2" in first[3][0].content
    assert generator._validate_artifacts("cloudfoundry", first[3])[0].severity == "INFO"


def test_mta_rejects_duplicate_identifiers_and_reports_missing_dependency(proposal) -> None:
    generator = InfrastructurePlanService()
    request = InfrastructurePlanRequirements(architecture_proposal_id=proposal.proposal_id, output_format="mta", mta_modules=[InfrastructureMtaModule(name="api", module_type="nodejs", path="srv", requires=["missing"])])
    missing: list[str] = []
    generator._render_mta(request, proposal, None, [], missing, [])
    assert any("missing" in item for item in missing)
    with pytest.raises(ValidationError):
        InfrastructurePlanRequirements(architecture_proposal_id=proposal.proposal_id, output_format="mta", mta_modules=[InfrastructureMtaModule(name="api", module_type="nodejs"), InfrastructureMtaModule(name="api", module_type="nodejs")])


def test_terraform_is_allowlisted_and_never_contains_credentials(proposal) -> None:
    generator = InfrastructurePlanService()
    request = InfrastructurePlanRequirements(architecture_proposal_id=proposal.proposal_id, output_format="terraform", terraform_subaccounts=[InfrastructureTerraformSubaccount(name="Development", subdomain="development", region="eu10")])
    rendered = generator._render_tf(request, proposal, [], [], [])
    content = rendered[3][0].content
    assert 'resource "btp_subaccount" "development"' in content
    assert "password" not in content.lower()
    assert not any(item.severity == "ERROR" for item in generator._validate_artifacts("terraform", rendered[3]))


def test_input_safety_rejects_routes_paths_and_unsafe_hcl() -> None:
    with pytest.raises(ValidationError):
        InfrastructureApplicationInput(name="api", routes=["${malicious}"])
    with pytest.raises(ValidationError):
        InfrastructureApplicationInput(name="api", path="../outside")
    with pytest.raises(ValidationError):
        InfrastructurePlanRequirements(architecture_proposal_id=uuid.uuid4(), output_format="terraform", terraform_subaccounts=[])
