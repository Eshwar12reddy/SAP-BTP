import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import type { ArchitectureProposal } from '../types/api'
import { FakeBackend } from '../test/fakeBackend'

const proposal = {
  proposal_id: 'proposal-1', revision: 1, title: 'SAP BTP architecture for employee app', overview: 'Reviewable target architecture.',
  requirements: { project_name: 'Employee app', business_objective: 'Manage employees', application_type: 'business application', user_count: 1000, existing_sap_systems: ['SAP S/4HANA'], external_systems: [], integration_requirements: ['SAP S/4HANA integration'], database_requirements: ['relational database'], runtime_preference: 'cloudfoundry', authentication_requirements: ['login'], authorization_requirements: [], preferred_region: null, availability_objectives: null, recovery_objectives: null, expected_traffic: null, missing_information: [] },
  runtime_options: [{ runtime_id: 'cloudfoundry', name: 'Cloud Foundry runtime', suitability: 'SELECTED', rationale: 'Fit', prerequisites: [], operational_considerations: [], restrictions: [], evidence_references: [], verification_status: 'DOCUMENTED' }],
  components: [{ component_id: 'application-backend', name: 'Application backend', component_type: 'APPLICATION', purpose: 'Business logic', related_service_key: null, environment_id: 'production', dependencies: [], evidence_references: [], verification_status: 'PROPOSED' }],
  connections: [], data_flows: [], environments: [{ environment_id: 'production', name: 'Production', stage: 'PRODUCTION', runtime_id: 'cloudfoundry', purpose: 'Production', account_structure: 'Governed boundary', status: 'PROPOSED', evidence_references: [] }],
  security_controls: [{ control_id: 'auth', control: 'Authentication', applies_to_component_ids: ['application-backend'], recommendation: 'Validate tokens', implementation_status: 'RECOMMENDED', evidence_references: [] }],
  risks: [{ risk_id: 'region', risk: 'Region is unverified', severity: 'HIGH', mitigation: 'Verify region', evidence_references: [] }],
  assumptions: [{ assumption_id: 'a1', statement: 'This is a proposal', impact: 'MEDIUM', requires_confirmation: true }],
  alternatives: [{ alternative_id: 'alternative-kyma', name: 'Kyma runtime', change_from_primary: 'Use Kyma', benefits: ['Kubernetes model'], drawbacks: ['More operations'], prerequisites: [], reliability_security_implications: [], evidence_references: [] }],
  cost_considerations: ['No price or plan was inferred.'], evidence: [{ evidence_id: 'sap', title: 'SAP guidance', url: 'https://help.sap.com/docs/sap-btp-guidance-framework', evidence_type: 'OFFICIAL_DOCUMENTATION', verification_status: 'VERIFIED', observed_at: null, supports: ['guidance'] }],
  diagram: { format: 'MERMAID_FLOWCHART', source: 'SERVER_GENERATED', mermaid: 'flowchart LR\nA[Backend]' }, validation: { valid: true, findings: [], validated_at: '2026-11-05T00:00:00Z' }, generated_at: '2026-11-05T00:00:00Z', account_id: null, environment_id: null,
} as ArchitectureProposal

describe('ArchitectureAdvisorPage', () => {
  it('generates, renders and compares a structured proposal', async () => {
    const backend = new FakeBackend().install()
    backend.addUser('viewer@example.com', ['viewer'], { name: 'Viewer' })
    backend.signIn('viewer@example.com')
    vi.spyOn(api, 'createArchitectureProposal').mockResolvedValue(proposal)
    vi.spyOn(api, 'compareArchitectureProposal').mockResolvedValue({ primary_proposal_id: proposal.proposal_id, compared_alternatives: proposal.alternatives, criteria: ['capability fit'], missing_information: [], generated_at: proposal.generated_at })
    window.history.replaceState(null, '', '/architecture')
    render(<App />)

    await userEvent.type(await screen.findByLabelText('Project and application requirements'), 'Employee app connected to S/4HANA')
    await userEvent.click(screen.getByRole('button', { name: 'Generate architecture' }))
    expect(await screen.findByText('SAP BTP architecture for employee app')).toBeInTheDocument()
    expect(screen.getByText(/flowchart LR/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
    expect(await screen.findByRole('heading', { name: 'Alternative comparison' })).toBeInTheDocument()
    expect(api.compareArchitectureProposal).toHaveBeenCalled()
  }, 15_000)
})
