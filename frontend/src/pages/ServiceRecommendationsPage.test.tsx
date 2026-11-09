import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import type { ServiceRecommendationResponse } from '../types/api'
import { FakeBackend } from '../test/fakeBackend'

const result: ServiceRecommendationResponse = {
  analysis: {
    requirements: { business_objective: 'Employee app', application_type: 'application', required_capabilities: ['database'], runtime_preference: 'unknown', region: null, existing_services: [], unknown_requirements: [], explicit_assumptions: [] },
    extracted_capabilities: ['database'], missing_critical_information: [], assumptions: [], analysis_status: 'READY', source: 'USER_MESSAGE',
  },
  recommendations: [
    { recommendation_id: 'rec-hana-cloud', service_name: 'SAP HANA Cloud', verified_identifier: 'hana-cloud', related_user_requirements: ['database'], recommended_capability: 'database-persistence', suggested_plan: null, suitability_explanation: 'It provides documented relational persistence.', eligibility_status: 'SUPPORTED', runtime_status: 'NOT_REQUESTED', region_status: 'NOT_REQUESTED', entitlement_status: 'NOT_VERIFIED', quota_status: 'NOT_CHECKED', prerequisites: ['Configure secure connectivity'], relevant_alternatives: [], tradeoffs: ['Sizing requires verification'], evidence_references: [{ source_type: 'DOCUMENTATION', reference: 'https://help.sap.com/docs/hana-cloud', observed_at: '2026-11-04T00:00:00Z', verification_status: 'VERIFIED' }], suitability_confidence: 'HIGH', availability_confidence: 'NOT_ASSESSED', assumptions: [], missing_information: [], source_verification_time: '2026-11-04T00:00:00Z' },
    { recommendation_id: 'rec-integration-suite', service_name: 'SAP Integration Suite', verified_identifier: 'integration-suite', related_user_requirements: ['database'], recommended_capability: 'integration', suggested_plan: null, suitability_explanation: 'It provides documented integration capabilities.', eligibility_status: 'UNKNOWN', runtime_status: 'NOT_REQUESTED', region_status: 'NOT_REQUESTED', entitlement_status: 'NOT_VERIFIED', quota_status: 'NOT_CHECKED', prerequisites: [], relevant_alternatives: [], tradeoffs: [], evidence_references: [{ source_type: 'DOCUMENTATION', reference: 'https://help.sap.com/docs/integration-suite', observed_at: '2026-11-04T00:00:00Z', verification_status: 'VERIFIED' }], suitability_confidence: 'MEDIUM', availability_confidence: 'NOT_ASSESSED', assumptions: [], missing_information: [], source_verification_time: '2026-11-04T00:00:00Z' },
  ],
  no_suitable_options: false, partial_evidence: false, catalog_version: 'v1.0', live_catalog: { retrieval_status: 'NOT_REQUESTED', environment_id: null, observed_at: null, provider: null, notes: [] }, generated_at: '2026-11-04T00:00:00Z',
}

describe('ServiceRecommendationsPage', () => {
  it('renders real recommendation evidence and requests a comparison', async () => {
    const backend = new FakeBackend().install()
    backend.addUser('viewer@example.com', ['viewer'], { name: 'Viewer' })
    backend.signIn('viewer@example.com')
    vi.spyOn(api, 'recommendServices').mockResolvedValue(result)
    vi.spyOn(api, 'compareServices').mockResolvedValue({ rows: result.recommendations.map((item) => ({ service_key: item.verified_identifier!, service_name: item.service_name, capability_fit: item.suitability_explanation, meaningful_difference: 'Different documented scope', runtime_status: item.runtime_status, region_status: item.region_status, entitlement_status: item.entitlement_status, prerequisites: item.prerequisites, evidence_references: item.evidence_references })), criteria: ['documented fit'], missing_information: [], generated_at: result.generated_at })
    window.history.replaceState(null, '', '/recommendations')
    render(<App />)

    await userEvent.type(await screen.findByLabelText('Requirements'), 'Need a database')
    await userEvent.click(screen.getByRole('button', { name: 'Recommend services' }))
    expect(await screen.findByText('SAP HANA Cloud')).toBeInTheDocument()
    const compareBoxes = screen.getAllByLabelText('Compare')
    await userEvent.click(compareBoxes[0])
    await userEvent.click(compareBoxes[1])
    await userEvent.click(screen.getByRole('button', { name: 'Compare selected (2)' }))
    expect(await screen.findByRole('heading', { name: 'Comparison' })).toBeInTheDocument()
    expect(api.compareServices).toHaveBeenCalled()
  })
})
