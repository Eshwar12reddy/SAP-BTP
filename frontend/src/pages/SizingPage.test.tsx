import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import type { SizingResult } from '../types/api'
import { FakeBackend } from '../test/fakeBackend'

const result = {
  result_id: 'result-1', scenario_id: 'baseline', formula_version: 'phase23-v1', runtime_type: 'cloudfoundry', application_name: 'Sizing request', architecture_proposal_id: null, architecture_proposal_revision: null,
  calculations: [{ calculation_id: 'memory', resource_reference: 'cloud-foundry-processes', calculation_type: 'CONFIGURED_MEMORY', input_values: { memory_total_before_margin: '1536' }, input_units: { memory_total_before_margin: 'MB' }, input_provenance: { memory_total_before_margin: 'DERIVED' }, formula: { formula_id: 'cf.configured_memory', formula_version: 'phase23-v1', expression: 'sum(memory_per_instance × instance_count)', description: 'configured' }, intermediate_calculations: [], final_value: '1536', result_unit: 'MB', assumptions: [], safety_margin_percent: '0', additional_capacity: '0', evidence_references: [], observation_time: null, verification_status: 'CALCULATED', warnings: [], missing_inputs: [], scenario_id: 'baseline' }],
  quota_impacts: [{ dimension: 'MEMORY', quota_state: 'UNKNOWN', quota_limit: null, quota_unit: 'MB', existing_usage: null, additional_allocation: '1536', projected_usage: null, remaining_capacity: null, projected_utilization_percent: null, compatible: null, evidence_references: [], observation_time: null, warnings: ['Quota unverified'] }], assumptions: [], warnings: [], missing_inputs: [], status: 'COMPLETE', generated_at: '2026-11-05T00:00:00Z',
} as SizingResult

describe('SizingPage', () => {
  it('calculates and compares transparent resource scenarios', async () => {
    const backend = new FakeBackend().install()
    backend.addUser('viewer@example.com', ['viewer'], { name: 'Viewer' })
    backend.signIn('viewer@example.com')
    vi.spyOn(api, 'calculateSizing').mockResolvedValue(result)
    vi.spyOn(api, 'compareSizingScenarios').mockResolvedValue({ comparison_id: 'comparison-1', formula_version: 'phase23-v1', results: [result], compared_dimensions: ['configured memory'], generated_at: result.generated_at })
    window.history.replaceState(null, '', '/sizing')
    render(<App />)

    await userEvent.type(await screen.findByLabelText('Sizing request'), 'Calculate memory for 3 instances using 512 MB each')
    await userEvent.click(screen.getByRole('button', { name: 'Calculate resources' }))
    expect(await screen.findByText('1536 MB')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Compare double traffic' }))
    expect(await screen.findByRole('heading', { name: 'Scenario comparison' })).toBeInTheDocument()
    expect(api.compareSizingScenarios).toHaveBeenCalled()
  }, 15_000)
})
