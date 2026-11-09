"""Deterministic Phase 21 recommendation contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.contracts import Route
from app.agents.router import route_by_rules
from app.schemas.recommendations import (
    RecommendationRequest,
    ServiceComparisonRequest,
    ServiceRequirements,
)
from app.services.service_recommendation_service import (
    ServiceRecommendationService,
    analyze_requirements,
)
from app.services.service_reference_catalog import (
    CATALOG_BY_KEY,
    REFERENCE_CATALOG,
    validate_reference_catalog,
)


def test_reference_catalog_is_small_verified_and_has_no_prices_or_fake_alternatives() -> None:
    validate_reference_catalog()
    assert len(REFERENCE_CATALOG) >= 10
    assert all("price" not in entry.model_dump_json().lower() for entry in REFERENCE_CATALOG)
    assert all(item.official_documentation_url.host == "help.sap.com" for item in REFERENCE_CATALOG)
    assert all(alternative in CATALOG_BY_KEY for item in REFERENCE_CATALOG for alternative in item.alternative_service_keys)


def test_requirements_analysis_extracts_explicit_capabilities_without_fabricating_workload() -> None:
    result = analyze_requirements("Build an employee management application integrated with SAP S/4HANA with secure identity and events.")
    assert {"application-development", "integration", "identity-security", "messaging-events"} <= set(result.extracted_capabilities)
    assert result.requirements.runtime_preference == "unknown"
    assert result.requirements.expected_workload is None
    assert "workload characteristics" in result.missing_critical_information


@pytest.mark.asyncio
async def test_database_and_s4_recommendations_are_documented_and_plan_free() -> None:
    result = await ServiceRecommendationService().recommend(RecommendationRequest(request_text="I need a secure database for an SAP extension integrated with S/4HANA."))
    names = [item.service_name for item in result.recommendations]
    assert "SAP HANA Cloud" in names
    assert "SAP Integration Suite" in names
    assert all(item.suggested_plan is None for item in result.recommendations)
    assert all(item.entitlement_status == "NOT_VERIFIED" for item in result.recommendations)
    assert all(item.evidence_references[0].verification_status == "VERIFIED" for item in result.recommendations)


@pytest.mark.asyncio
async def test_runtime_and_region_unknown_are_not_promoted_to_supported() -> None:
    requirements = ServiceRequirements(required_capabilities=["application-development"], runtime_preference="kyma", region="eu99")
    result = await ServiceRecommendationService().recommend(RecommendationRequest(requirements=requirements))
    kyma = next(item for item in result.recommendations if item.service_name == "Kyma runtime")
    assert all(item.service_name != "Cloud Foundry runtime" for item in result.recommendations)
    assert kyma.runtime_status == "SUPPORTED"
    assert kyma.region_status == "UNKNOWN"
    assert kyma.eligibility_status == "UNKNOWN"


@pytest.mark.asyncio
async def test_existing_service_reuse_is_explained_without_verifying_it() -> None:
    requirements = ServiceRequirements(required_capabilities=["database"], existing_services=["SAP HANA Cloud"])
    result = await ServiceRecommendationService().recommend(RecommendationRequest(requirements=requirements))
    item = result.recommendations[0]
    assert item.entitlement_status == "EXISTING_SERVICE_NOT_VERIFIED"
    assert "reuse" in item.suitability_explanation.lower()


def test_comparison_rejects_fabricated_service_and_returns_source_links() -> None:
    service = ServiceRecommendationService()
    with pytest.raises(Exception) as error:
        service.compare(ServiceComparisonRequest(service_keys=["hana-cloud", "invented-service"]))
    assert getattr(error.value, "code", None) == "invalid_catalog_service"
    result = service.compare(ServiceComparisonRequest(service_keys=["hana-cloud", "integration-suite"], request_text="S/4HANA integration"))
    assert len(result.rows) == 2
    assert all(row.evidence_references[0].reference.startswith("https://help.sap.com/") for row in result.rows)


def test_request_contract_requires_text_or_typed_requirements() -> None:
    with pytest.raises(ValidationError):
        RecommendationRequest()


def test_recommendation_route_is_distinct_from_generic_architecture() -> None:
    assert route_by_rules("Which SAP BTP service should I use for a database?") is Route.BTP_RECOMMENDATIONS
    assert route_by_rules("Recommend an integration architecture.") is Route.ARCHITECTURE

