import { useState, type FormEvent } from 'react'
import { PageShell } from '../components/AppNav'
import { ApiError, api } from '../services/api'
import type { SizingComparisonResponse, SizingResult } from '../types/api'

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : 'The deterministic sizing service is unavailable.'
}

function numberValue(value: string): number | undefined {
  const parsed = Number(value)
  return value.trim() && Number.isFinite(parsed) ? parsed : undefined
}

function reportText(result: SizingResult): string {
  return [
    `Deterministic sizing ${result.scenario_id}`,
    `Status: ${result.status}`,
    ...result.calculations.map((item) => `${item.calculation_type}: ${item.final_value ?? 'unknown'} ${item.result_unit} — ${item.formula.expression}`),
    ...result.quota_impacts.map((item) => `Quota ${item.dimension}: ${item.quota_state}; remaining ${item.remaining_capacity ?? 'unknown'} ${item.quota_unit}`),
    `Missing inputs: ${result.missing_inputs.join('; ') || 'none'}`,
    `Warnings: ${result.warnings.join('; ') || 'none'}`,
  ].join('\n')
}

export function SizingPage() {
  const queryProposalId = new URLSearchParams(window.location.search).get('proposal_id') ?? ''
  const [requestText, setRequestText] = useState('')
  const [proposalId, setProposalId] = useState(queryProposalId)
  const [runtime, setRuntime] = useState('cloudfoundry')
  const [users, setUsers] = useState('')
  const [instances, setInstances] = useState('')
  const [memory, setMemory] = useState('')
  const [disk, setDisk] = useState('')
  const [peak, setPeak] = useState('')
  const [sustainable, setSustainable] = useState('')
  const [dataVolume, setDataVolume] = useState('')
  const [growth, setGrowth] = useState('')
  const [projectionDays, setProjectionDays] = useState('365')
  const [safetyMargin, setSafetyMargin] = useState('0')
  const [quotaMemory, setQuotaMemory] = useState('')
  const [quotaUsage, setQuotaUsage] = useState('')
  const [result, setResult] = useState<SizingResult | null>(null)
  const [comparison, setComparison] = useState<SizingComparisonResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const requirements = () => {
    const value: Record<string, unknown> = { runtime_type: runtime, application_name: 'Sizing request', safety_margin_percent: numberValue(safetyMargin) ?? 0 }
    const mapping: Array<[string, string, string]> = [
      ['user_count', users, 'number'], ['instance_count', instances, 'number'], ['configured_memory_per_instance', memory, 'number'],
      ['configured_disk_per_instance', disk, 'number'], ['peak_requests_per_second', peak, 'number'], ['sustainable_requests_per_second_per_instance', sustainable, 'number'],
      ['data_volume', dataVolume, 'number'], ['expected_growth_per_period', growth, 'number'], ['projection_period_days', projectionDays, 'number'],
      ['quota_memory_limit', quotaMemory, 'number'], ['quota_memory_usage', quotaUsage, 'number'],
    ]
    for (const [key, text, type] of mapping) {
      const parsed = numberValue(text)
      if (parsed !== undefined) value[key] = type === 'number' ? parsed : text
    }
    if (quotaMemory || quotaUsage) {
      value.quota_memory_state = 'FINITE'
      value.quota_memory_unit = 'MB'
      value.quota_source = 'USER_PROVIDED'
    }
    if (growth) value.growth_period_days = 365
    if (proposalId.trim()) value.architecture_proposal_id = proposalId.trim()
    return value
  }

  const calculate = async (event: FormEvent) => {
    event.preventDefault()
    const hasStructuredInputs = Boolean(proposalId.trim() || users || instances || memory || disk || peak || sustainable || dataVolume || growth || quotaMemory || quotaUsage || safetyMargin !== '0' || runtime !== 'cloudfoundry')
    if (!requestText.trim() && !hasStructuredInputs) return
    setLoading(true); setError(null); setComparison(null)
    try {
      setResult(await api.calculateSizing({ request_text: requestText.trim() || undefined, requirements: hasStructuredInputs ? requirements() : undefined, persist: true }))
    } catch (e) { setError(errorText(e)) } finally { setLoading(false) }
  }

  const compareGrowth = async () => {
    if (!result) return
    const base = requirements()
    const currentPeak = numberValue(peak)
    try {
      setComparison(await api.compareSizingScenarios([
        { scenario_id: 'baseline', name: 'Baseline', requirements: base },
        { scenario_id: 'double-traffic', name: 'Double traffic', requirements: { ...base, peak_requests_per_second: currentPeak === undefined ? undefined : currentPeak * 2 } },
      ]))
    } catch (e) { setError(errorText(e)) }
  }

  const copy = async () => { if (result) await navigator.clipboard.writeText(reportText(result)) }
  const exportReport = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([reportText(result)], { type: 'text/plain' }))
    const link = document.createElement('a'); link.href = url; link.download = `btp-sizing-${result.result_id}.txt`; link.click(); URL.revokeObjectURL(url)
  }

  return <PageShell view="sizing" title="Sizing calculator">
    <p className="muted">Calculate transparent memory, instance, disk, storage and quota scenarios from explicit inputs. User count alone never determines capacity.</p>
    <section className="card"><form className="form" onSubmit={calculate} aria-label="Sizing requirements">
      <label className="field"><span className="field__label">Sizing request</span><textarea className="input" rows={4} value={requestText} onChange={(event) => setRequestText(event.target.value)} placeholder="Example: Calculate memory for 3 Cloud Foundry instances using 512 MB each." /></label>
      <label className="field"><span className="field__label">Architecture proposal ID (optional)</span><input className="input" value={proposalId} onChange={(event) => setProposalId(event.target.value)} placeholder="Authorized proposal UUID" /></label>
      <div className="form-grid">
        <label className="field"><span className="field__label">Runtime</span><select className="input" value={runtime} onChange={(event) => setRuntime(event.target.value)}><option value="cloudfoundry">Cloud Foundry</option><option value="kyma">Kyma</option><option value="abap">ABAP environment</option><option value="hana-cloud">HANA Cloud</option><option value="integration">Integration or messaging</option></select></label>
        <label className="field"><span className="field__label">Users</span><input className="input" inputMode="numeric" value={users} onChange={(event) => setUsers(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Instances</span><input className="input" inputMode="numeric" value={instances} onChange={(event) => setInstances(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Memory per instance (MB)</span><input className="input" inputMode="decimal" value={memory} onChange={(event) => setMemory(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Disk per instance (MB)</span><input className="input" inputMode="decimal" value={disk} onChange={(event) => setDisk(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Peak RPS</span><input className="input" inputMode="decimal" value={peak} onChange={(event) => setPeak(event.target.value)} placeholder="Requires benchmark" /></label>
        <label className="field"><span className="field__label">Sustainable RPS / instance</span><input className="input" inputMode="decimal" value={sustainable} onChange={(event) => setSustainable(event.target.value)} placeholder="Measured only" /></label>
        <label className="field"><span className="field__label">Logical data volume (GB)</span><input className="input" inputMode="decimal" value={dataVolume} onChange={(event) => setDataVolume(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Growth (GB / period)</span><input className="input" inputMode="decimal" value={growth} onChange={(event) => setGrowth(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Projection days</span><input className="input" inputMode="numeric" value={projectionDays} onChange={(event) => setProjectionDays(event.target.value)} /></label>
        <label className="field"><span className="field__label">Safety margin (%)</span><input className="input" inputMode="decimal" value={safetyMargin} onChange={(event) => setSafetyMargin(event.target.value)} /></label>
        <label className="field"><span className="field__label">Quota memory (MB)</span><input className="input" inputMode="decimal" value={quotaMemory} onChange={(event) => setQuotaMemory(event.target.value)} placeholder="Optional" /></label>
        <label className="field"><span className="field__label">Current quota usage (MB)</span><input className="input" inputMode="decimal" value={quotaUsage} onChange={(event) => setQuotaUsage(event.target.value)} placeholder="Optional" /></label>
      </div>
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      <button type="submit" className="button button--primary" disabled={loading}>{loading ? 'Calculating…' : 'Calculate resources'}</button>
    </form></section>
    {result && <SizingResultView result={result} onCompare={() => void compareGrowth()} onCopy={() => void copy()} onExport={exportReport} />}
    {comparison && <ComparisonView comparison={comparison} />}
  </PageShell>
}

function SizingResultView({ result, onCompare, onCopy, onExport }: { result: SizingResult; onCompare: () => void; onCopy: () => void; onExport: () => void }) {
  return <section className="card" aria-label="Sizing result"><div className="detail__header"><div><h2 className="card__title">Scenario: {result.scenario_id}</h2><p className="muted">Formula version {result.formula_version} · {result.status}</p></div><span className="badge badge--ok">Deterministic</span></div>
    <h3 className="section-title">Calculated resources</h3><div className="inventory-grid">{result.calculations.map((item) => <article className="card" key={item.calculation_id}><h4>{item.calculation_type}</h4><p className="metric-value">{item.final_value ?? 'Unknown'} {item.final_value === null ? '' : item.result_unit}</p><p>{item.formula.expression}</p>{item.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}</article>)}</div>
    <h3 className="section-title">Quota impact</h3><ul>{result.quota_impacts.map((item) => <li key={item.dimension}><strong>{item.dimension}:</strong> {item.quota_state}; remaining {item.remaining_capacity ?? 'unknown'} {item.quota_unit}; utilization {item.projected_utilization_percent ?? 'unknown'}</li>)}</ul>
    <h3 className="section-title">Assumptions and missing evidence</h3><ul>{result.assumptions.map((item) => <li key={item.assumption_id}>{item.statement}</li>)}{result.missing_inputs.map((item) => <li key={item}><strong>Missing:</strong> {item}</li>)}{result.warnings.map((item) => <li key={item}><strong>Warning:</strong> {item}</li>)}</ul>
    <div className="form__actions"><button type="button" className="button button--primary" onClick={onCompare}>Compare double traffic</button><button type="button" className="button button--ghost" onClick={onCopy}>Copy report</button><button type="button" className="button button--ghost" onClick={onExport}>Export report</button></div>
  </section>
}

function ComparisonView({ comparison }: { comparison: SizingComparisonResponse }) {
  return <section className="card" aria-label="Sizing comparison"><h2 className="card__title">Scenario comparison</h2><div className="inventory-grid">{comparison.results.map((result) => <article className="card" key={result.scenario_id}><h3 className="section-title">{result.scenario_id}</h3>{result.calculations.filter((item) => item.final_value !== null).map((item) => <p key={item.calculation_id}><strong>{item.calculation_type}:</strong> {item.final_value} {item.result_unit}</p>)}</article>)}</div></section>
}
