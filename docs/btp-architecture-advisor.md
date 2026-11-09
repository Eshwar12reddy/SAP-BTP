# SAP BTP AI Architecture Advisor

Phase 22 adds a reviewable architecture-design capability on top of the deterministic Phase 21 service recommendation engine.

## Contract

The advisor first converts a request into typed `ArchitectureRequirements`. Every extracted item is treated as one of:

- explicit requirement supplied by the user;
- verified context supplied by an authorized application context or evidence source;
- assumption that requires confirmation; or
- unknown information that produces a clarification question.

Unknown region, workload, availability, recovery, budget, entitlement and quota facts are not silently promoted into architecture facts.

## Proposal model

An `ArchitectureProposal` contains runtime options, application and service components, connections, data flows, development/test/production environments, security controls, risks, assumptions, alternatives, cost considerations, evidence links, validation findings and a server-generated Mermaid flowchart. Component and edge references are validated before a proposal is returned or persisted.

The proposal is a logical target design. It does not provision services, create accounts, assign entitlements, deploy applications, or claim pricing, plan, capacity, region or quota facts.

## API

The API is under `/api/v1/btp/architecture-advisor`:

- `POST /requirements/analyze` — extract requirements and focused clarification questions.
- `POST /proposals` — generate and persist revision 1.
- `GET /proposals` and `GET /proposals/{id}` — retrieve owner-scoped proposals.
- `POST /proposals/{id}/revisions` — create an immutable new revision.
- `POST /validate` — validate a proposal without persisting it.
- `POST /compare` — compare documented alternatives and missing information.
- `GET /proposals/{id}/graph` — retrieve the validated server-generated graph.

All proposal records are scoped to the authenticated owner and re-authorized account/environment context. Revisions are append-only rows in `architecture_proposals`; unverified assumptions are proposal content, not long-term confirmed memory.

## Agent and tools

The single-agent workflow has a dedicated `BTP_ARCHITECTURE` route and calls only these read-only tools:

- `btp_architecture_requirements_analyze`
- `btp_architecture_design`
- `btp_architecture_validate`
- `btp_architecture_compare`

The server creates the component graph and Mermaid source. Model output is never executed as graph syntax or infrastructure instructions.

## Evidence boundary

The design references official SAP documentation for the Guidance Framework, account model, environments and integration architecture. When explicitly requested and authorized, provider catalog visibility may be included as partial live evidence; provider visibility is not entitlement, quota or pricing proof.

Relevant official references:

- [SAP BTP Guidance Framework](https://help.sap.com/docs/sap-btp-guidance-framework)
- [SAP BTP account model](https://help.sap.com/docs/btp/sap-business-technology-platform/account-model)
- [SAP BTP environments](https://help.sap.com/docs/btp/sap-business-technology-platform/environments)
- [SAP integration architecture guide](https://help.sap.com/docs/sap-btp-guidance-framework/integration-architecture-guide)
- [Cloud Foundry v3 API reference](https://v3-apidocs.cloudfoundry.org/version/3.150.0/index.html)

## Deliberate limits

Sizing, price calculation, deployment planning, entitlement activation and account mutation remain outside Phase 22. The advisor produces a safe architecture proposal and identifies the evidence that must be collected before implementation.
