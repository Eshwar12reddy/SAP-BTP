# SAP BTP infrastructure plan generator

Phase 24 adds a review-only configuration generator. It converts an owned Phase 22
architecture proposal and, when supplied, an owned Phase 23 sizing result into bounded
configuration artifacts. It never deploys, provisions, applies, pushes, builds, calls a
SAP provider, or writes to Git.

## Supported formats

- Cloud Foundry `manifest.yml`: application name, sizing-derived memory/instances/disk,
  explicitly supplied path, command, buildpacks, stack, routes, service binding names,
  and safe environment-variable placeholders.
- MTA `mta.yaml`: explicit application ID/version, module type/path, `requires` and
  `provides` references, and explicitly supplied resource declarations. Missing module
  build inputs keep the plan partial/incomplete.
- SAP/btp Terraform: a pinned provider block and the verified subset
  `btp_subaccount`, `btp_subaccount_entitlement`, and
  `btp_subaccount_service_instance`. Provider credentials and parameters are excluded.

The supported mappings are server-controlled. A component that has no verified mapping
is reported as unsupported; it is not silently omitted.

## Deterministic workflow

1. Re-authorize and load the owner-scoped architecture revision.
2. Optionally load the owner-scoped sizing result and preserve its formula version,
   calculations, provenance, and quota impact.
3. Check revision and environment relationships.
4. Render from typed inputs through allowlisted serializers.
5. Perform local syntax and reference checks.
6. Produce a review report and persist an immutable plan revision.

DeepSeek is not in the rendering path. The same validated inputs and generator version
produce equivalent artifacts, apart from server timestamps and the first generated plan ID.

## Validation boundary

Cloud Foundry and MTA artifacts are parsed as YAML and checked for allowed fields,
identifiers, list structure, and references. Terraform HCL is checked structurally for
allowed resource types, identifiers, and unsafe interpolation. A Terraform provider
download, `terraform init`, `terraform plan`, or remote state comparison is not performed,
so HCL parsing is not an operational provider validation.

## Security

Generated output never contains passwords, tokens, API keys, private keys, credential JSON,
or raw destination credentials. Environment variable names are validated and values become
`((NAME))` placeholders. Paths, routes, filenames, YAML keys, and HCL identifiers are
bounded and validated. The export package contains only generated artifacts and the review
report.

## Persistence and API

`infrastructure_plans` stores owner, scope, source revisions, requirements, plan JSON and
immutable revision metadata. APIs are under `/api/v1/btp/infrastructure-plans`:

- `POST /requirements/analyze`
- `POST /generate`
- `POST /validate`
- `GET /{plan_id}`
- `GET /{plan_id}/revisions`
- `POST /{plan_id}/compare`
- `GET /{plan_id}/artifacts/{filename}`
- `GET /{plan_id}/export`

All persisted reads are owner-scoped and authenticated. Architecture and environment scope
checks continue to use existing RBAC coverage. Generation does not enter the four-option
execution approval flow because it is a static text artifact.

## Official references

- [Cloud Foundry manifest attributes](https://docs.cloudfoundry.org/devguide/deploy-apps/manifest-attributes.html)
- [Cloud Foundry manifests](https://docs.cloudfoundry.org/devguide/deploy-apps/manifest.html)
- [SAP MTA documentation](https://help.sap.com/docs/btp/sap-business-technology-platform/multitarget-applications-in-cloud-foundry-environment)
- [SAP/btp provider](https://registry.terraform.io/providers/SAP/btp/latest/docs)
- [SAP/btp subaccount](https://registry.terraform.io/providers/SAP/btp/latest/docs/resources/subaccount)
- [SAP/btp entitlement](https://registry.terraform.io/providers/SAP/btp/latest/docs/resources/subaccount_entitlement)
- [SAP/btp service instance](https://registry.terraform.io/providers/SAP/btp/latest/docs/resources/subaccount_service_instance)

## Known limitations

No live provider state comparison, entitlement lookup, service-plan lookup, MBT build,
Cloud Foundry push, Terraform validation against a downloaded provider, secret injection,
or deployment-readiness claim is made. Phase 25 owns state-aware previews.
