"""Small, versioned SAP BTP reference catalog.

This catalog is intentionally separate from the live Cloud Foundry catalog.  It contains
only service families and documented capability relationships; it contains no prices,
entitlements or invented plans.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.recommendations import ServiceCatalogEntry

CATALOG_VERSION = "v1.0"
CATALOG_VERIFIED_AT = datetime(2026, 11, 4, tzinfo=UTC)


def _entry(**values) -> ServiceCatalogEntry:
    return ServiceCatalogEntry(
        catalog_version=CATALOG_VERSION,
        source_verification_timestamp=CATALOG_VERIFIED_AT,
        **values,
    )


REFERENCE_CATALOG: tuple[ServiceCatalogEntry, ...] = (
    _entry(
        service_key="cloud-foundry-runtime", service_name="Cloud Foundry runtime",
        technical_identifier="cloudfoundry", capability_category="application-development",
        supported_use_cases=["managed application runtime", "buildpack-based applications"],
        runtime_compatibility={"cloudfoundry": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/btp/sap-btp-cockpit/cloud-foundry-environment",
        availability_verification_status="REGION_UNKNOWN", limitations=["Region and quota availability are account-specific"],
        alternative_service_keys=["kyma-runtime"],
    ),
    _entry(
        service_key="kyma-runtime", service_name="Kyma runtime",
        technical_identifier="kyma", capability_category="application-development",
        supported_use_cases=["Kubernetes-based applications", "containerized extensions"],
        runtime_compatibility={"kyma": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/btp/sap-btp-kyma-runtime",
        availability_verification_status="REGION_UNKNOWN", limitations=["Kubernetes operational responsibilities and regional availability apply"],
        alternative_service_keys=["cloud-foundry-runtime"],
    ),
    _entry(
        service_key="abap-environment", service_name="SAP BTP, ABAP environment",
        technical_identifier="abap", capability_category="application-development",
        supported_use_cases=["ABAP cloud extensions", "ABAP-based application development"],
        runtime_compatibility={"abap": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/btp/sap-btp-abap-environment",
        availability_verification_status="REGION_UNKNOWN",
        limitations=["ABAP-specific development model, lifecycle and regional availability apply"],
        alternative_service_keys=["cloud-foundry-runtime", "kyma-runtime"],
    ),
    _entry(
        service_key="business-application-studio", service_name="SAP Business Application Studio",
        technical_identifier="business-application-studio", capability_category="application-development",
        supported_use_cases=["SAP application development", "extension development"],
        official_documentation_url="https://help.sap.com/docs/bas/sap-business-application-studio",
        availability_verification_status="REGION_UNKNOWN", limitations=["It is a development environment, not an application runtime"],
    ),
    _entry(
        service_key="hana-cloud", service_name="SAP HANA Cloud",
        technical_identifier="hana-cloud", capability_category="database-persistence",
        supported_use_cases=["relational persistence", "SAP HANA data and application workloads"],
        prerequisites=["Choose an account-supported HANA Cloud edition and configure secure application connectivity"],
        runtime_compatibility={"cloudfoundry": "DOCUMENTED", "kyma": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/hana-cloud",
        availability_verification_status="REGION_UNKNOWN", limitations=["Sizing, region, entitlement and commercial terms require account verification"],
        alternative_service_keys=[],
    ),
    _entry(
        service_key="integration-suite", service_name="SAP Integration Suite",
        technical_identifier="integration-suite", capability_category="integration",
        supported_use_cases=["application-to-application integration", "API management", "business-to-business integration"],
        prerequisites=["Select the required Integration Suite capability and configure the target-system connectivity"],
        official_documentation_url="https://help.sap.com/docs/integration-suite",
        availability_verification_status="REGION_UNKNOWN", limitations=["The suite contains capabilities with separate configuration and entitlement considerations"],
        alternative_service_keys=["destination-service", "connectivity-service", "event-mesh"],
    ),
    _entry(
        service_key="destination-service", service_name="SAP Destination service",
        technical_identifier="destination", capability_category="integration-connectivity",
        supported_use_cases=["externalized target-system connection properties", "OAuth token retrieval for remote services"],
        prerequisites=["Create and bind a Destination service instance", "Configure at least one destination", "Use XSUAA-backed authorization for application consumption where required"],
        runtime_compatibility={"cloudfoundry": "DOCUMENTED", "kyma": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/connectivity/sap-btp-connectivity-cf/consuming-destination-service",
        availability_verification_status="REGION_UNKNOWN", limitations=["Destination configuration and service entitlement are not inferred from documentation"],
        alternative_service_keys=["integration-suite", "connectivity-service"],
    ),
    _entry(
        service_key="connectivity-service", service_name="SAP Connectivity service",
        technical_identifier="connectivity", capability_category="integration-connectivity",
        supported_use_cases=["secure access to on-premise systems through Cloud Connector", "private network connectivity"],
        prerequisites=["Configure Cloud Connector or the documented connectivity path", "Create and bind a Connectivity service instance"],
        runtime_compatibility={"cloudfoundry": "DOCUMENTED", "kyma": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/connectivity",
        availability_verification_status="REGION_UNKNOWN", limitations=["Connectivity service does not replace target-system APIs or authorization"],
        alternative_service_keys=["destination-service", "integration-suite"],
    ),
    _entry(
        service_key="xsuaa", service_name="Authorization and Trust Management service",
        technical_identifier="xsuaa", capability_category="identity-security",
        supported_use_cases=["OAuth authorization", "application scopes and role templates", "application identity"],
        prerequisites=["Define application scopes and role templates", "Bind the service to the application"],
        runtime_compatibility={"cloudfoundry": "DOCUMENTED"},
        official_documentation_url="https://help.sap.com/docs/btp/sap-btp-security",
        availability_verification_status="REGION_UNKNOWN", limitations=["Tenant identity and trust configuration remain account-specific"],
        alternative_service_keys=["cloud-identity-services"],
    ),
    _entry(
        service_key="cloud-identity-services", service_name="SAP Cloud Identity Services",
        technical_identifier="cloud-identity-services", capability_category="identity-security",
        supported_use_cases=["identity lifecycle", "single sign-on", "identity federation"],
        official_documentation_url="https://help.sap.com/docs/cloud-identity-services",
        availability_verification_status="REGION_UNKNOWN", limitations=["Tenant, identity provider and user lifecycle configuration are not inferred"],
        alternative_service_keys=["xsuaa"],
    ),
    _entry(
        service_key="build-process-automation", service_name="SAP Build Process Automation",
        technical_identifier="build-process-automation", capability_category="automation-workflow",
        supported_use_cases=["workflow automation", "business rules", "process automation"],
        prerequisites=["Define the process, roles and integration endpoints"],
        official_documentation_url="https://help.sap.com/docs/build-process-automation",
        availability_verification_status="REGION_UNKNOWN", limitations=["Process content, connectors and tenant availability vary by scenario"],
        alternative_service_keys=["integration-suite"],
    ),
    _entry(
        service_key="event-mesh", service_name="SAP Event Mesh",
        technical_identifier="event-mesh", capability_category="messaging-events",
        supported_use_cases=["asynchronous business events", "queues and topic subscriptions"],
        prerequisites=["Create the service instance", "Configure queues, subscriptions and credentials"],
        official_documentation_url="https://help.sap.com/docs/event-mesh",
        availability_verification_status="REGION_UNKNOWN", limitations=["Event contracts, retention and quota details require account-specific verification"],
        alternative_service_keys=["integration-suite"],
    ),
    _entry(
        service_key="cloud-logging", service_name="SAP Cloud Logging",
        technical_identifier="cloud-logging", capability_category="monitoring-observability",
        supported_use_cases=["centralized application logs", "operational observability"],
        official_documentation_url="https://help.sap.com/docs/cloud-logging",
        availability_verification_status="REGION_UNKNOWN", limitations=["Retention, ingestion limits and regional availability require verification"],
    ),
    _entry(
        service_key="ai-core", service_name="SAP AI Core",
        technical_identifier="ai-core", capability_category="artificial-intelligence",
        supported_use_cases=["AI workload orchestration", "machine learning lifecycle workloads"],
        prerequisites=["Define the AI workload, model/runtime dependencies and data access pattern"],
        official_documentation_url="https://help.sap.com/docs/sap-ai-core",
        availability_verification_status="REGION_UNKNOWN", limitations=["Model availability, quotas and commercial terms are not inferred"],
        alternative_service_keys=["integration-suite"],
    ),
)

CATALOG_BY_KEY = {item.service_key: item for item in REFERENCE_CATALOG}


def get_catalog_entry(service_key: str) -> ServiceCatalogEntry | None:
    return CATALOG_BY_KEY.get(service_key.strip().lower())


def validate_reference_catalog() -> None:
    keys = set(CATALOG_BY_KEY)
    if len(keys) != len(REFERENCE_CATALOG):
        raise RuntimeError("reference catalog contains duplicate service keys")
    for item in REFERENCE_CATALOG:
        if any(alternative not in keys for alternative in item.alternative_service_keys):
            raise RuntimeError(f"catalog alternative is not a known service: {item.service_key}")


validate_reference_catalog()
