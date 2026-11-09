"""Safe API contracts for live Cloud Foundry observations."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: uuid.UUID
    connection_id: uuid.UUID
    observed_at: datetime
    evidence_origin: str = "cloud_foundry_v3"
    availability: str = "available"
    source_type: str = "LIVE_PROVIDER"
    retrieval_status: str = "AVAILABLE"


class ProviderPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    has_more: bool
    evidence: Evidence


class OrganizationRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "organization"
    display_name: str
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class OrganizationPage(ProviderPage):
    items: list[OrganizationRead]


class SpaceRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "space"
    display_name: str
    organization_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class SpacePage(ProviderPage):
    items: list[SpaceRead]


class ApplicationRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "application"
    display_name: str
    lifecycle_state: str | None
    lifecycle: str | None
    space_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ApplicationPage(ProviderPage):
    items: list[ApplicationRead]


class ProcessRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "process"
    process_type: str
    state: str | None
    instances: int | None = Field(default=None, ge=0)
    application_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ProcessPage(ProviderPage):
    items: list[ProcessRead]


class ProcessStatRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_type: str = "process_instance"
    process_type: str | None
    index: int | None = Field(default=None, ge=0)
    state: str | None
    observed_at: datetime | None
    cpu: float | None
    memory_bytes: int | None = Field(default=None, ge=0)
    disk_bytes: int | None = Field(default=None, ge=0)
    uptime_seconds: int | None = Field(default=None, ge=0)
    evidence: Evidence


class ProcessStatsRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    process_id: str
    items: list[ProcessStatRead]
    evidence: Evidence


class ServiceInstanceRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "service_instance"
    display_name: str
    space_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ServiceInstancePage(ProviderPage):
    items: list[ServiceInstanceRead]


class ServiceBindingRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "service_binding"
    binding_type: str
    application_id: str | None
    service_instance_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ServiceBindingPage(ProviderPage):
    items: list[ServiceBindingRead]


class ApplicationEventRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    event_type: str | None
    resource_type: str = "application_usage_event"
    application_id: str | None
    application_name: str | None
    process_type: str | None
    current_state: str | None
    previous_state: str | None
    instance_count: int | None = Field(default=None, ge=0)
    created_at: datetime | None
    evidence: Evidence


class ApplicationEventPage(ProviderPage):
    items: list[ApplicationEventRead]


class ServiceOfferingRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "service_offering"
    display_name: str
    description: str | None
    available: bool | None
    service_broker_id: str | None
    entitlement_status: str = "UNKNOWN_ENTITLEMENT"
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ServiceOfferingPage(ProviderPage):
    items: list[ServiceOfferingRead]


class ServicePlanRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "service_plan"
    display_name: str
    description: str | None
    available: bool | None
    service_offering_id: str | None
    service_broker_id: str | None
    entitlement_status: str = "UNKNOWN_ENTITLEMENT"
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class ServicePlanPage(ProviderPage):
    items: list[ServicePlanRead]


class QuotaValueRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: int | None
    state: str


class QuotaRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    resource_type: str = "quota"
    quota_type: str
    display_name: str
    organization_ids: list[str]
    space_ids: list[str]
    total_memory_mb: QuotaValueRead
    per_process_memory_mb: QuotaValueRead
    total_instances: QuotaValueRead
    per_app_tasks: QuotaValueRead
    total_service_instances: QuotaValueRead
    total_service_keys: QuotaValueRead
    total_routes: QuotaValueRead
    total_reserved_ports: QuotaValueRead
    total_domains: QuotaValueRead
    log_rate_limit_bytes_per_second: QuotaValueRead
    paid_services_allowed: bool | None
    created_at: datetime | None
    updated_at: datetime | None
    evidence: Evidence


class QuotaPage(ProviderPage):
    items: list[QuotaRead]


class UsageSummaryRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str
    resource_type: str
    started_instances: int | None
    memory_in_mb: int | None
    evidence: Evidence


class InventoryUnavailableRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[dict[str, object]] = Field(default_factory=list)
    has_more: bool = False
    evidence: Evidence | None = None
    retrieval_status: str
    reason: str


class InventoryStatusRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_type: str
    source_type: str
    retrieval_status: str
    observed_at: datetime
    environment_id: uuid.UUID | None
    reason: str
