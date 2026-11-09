import { useEffect, useState, type FormEvent } from 'react'
import { PageShell } from '../components/AppNav'
import { navigateTo } from '../routes/router'
import { ApiError, api } from '../services/api'
import type { ArchitectureProposal, InfrastructurePlan, SizingResult } from '../types/api'

function errorText(error: unknown): string { return error instanceof ApiError ? error.message : 'The infrastructure-plan service is unavailable.' }

export function InfrastructurePlansPage() {
  const queryProposalId = new URLSearchParams(window.location.search).get('proposal_id') ?? ''
  const [proposals, setProposals] = useState<ArchitectureProposal[]>([])
  const [sizing, setSizing] = useState<SizingResult[]>([])
  const [proposalId, setProposalId] = useState(queryProposalId)
  const [revision, setRevision] = useState('')
  const [sizingId, setSizingId] = useState('')
  const [format, setFormat] = useState<'cloudfoundry' | 'mta' | 'terraform'>('cloudfoundry')
  const [applicationName, setApplicationName] = useState('')
  const [environmentVariables, setEnvironmentVariables] = useState('')
  const [plan, setPlan] = useState<InfrastructurePlan | null>(null)
  const [compare, setCompare] = useState<{ left: string; right: string }>({ left: '1', right: '2' })
  const [comparisonText, setComparisonText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void Promise.all([api.listArchitectureProposals(), api.listSizingResults()]).then(([proposalPage, sizingPage]) => {
      setProposals(proposalPage.items); setSizing(sizingPage.items)
      const selected = proposalPage.items.find((item) => item.proposal_id === queryProposalId) ?? proposalPage.items[0]
      if (selected) { setProposalId(selected.proposal_id); setRevision(String(selected.revision)) }
    }).catch((reason) => setError(errorText(reason)))
  }, [])

  const selectedProposal = proposals.find((item) => item.proposal_id === proposalId)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!proposalId) return
    setLoading(true); setError(null); setComparisonText(null)
    const variables = environmentVariables.split(',').map((item) => item.trim()).filter(Boolean)
    try {
      setPlan(await api.generateInfrastructurePlan({ architecture_proposal_id: proposalId, architecture_proposal_revision: revision ? Number(revision) : undefined, sizing_result_id: sizingId || undefined, output_format: format, application_name: applicationName || undefined, applications: applicationName ? [{ name: applicationName, environment_variables: variables }] : undefined, persist: true }))
    } catch (reason) { setError(errorText(reason)) } finally { setLoading(false) }
  }
  const validate = async () => { if (!plan) return; setLoading(true); setError(null); try { setPlan(await api.validateInfrastructurePlan(plan)) } catch (reason) { setError(errorText(reason)) } finally { setLoading(false) } }
  const comparePlans = async () => { if (!plan) return; try { const result = await api.compareInfrastructurePlan(plan.plan_id, Number(compare.left), Number(compare.right)); setComparisonText(JSON.stringify(result, null, 2)) } catch (reason) { setError(errorText(reason)) } }
  const download = (filename: string, content: string) => { const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([content], { type: 'text/plain' })); link.download = filename; link.click() }
  const exportPackage = async () => { if (!plan) return; try { const blob = await api.exportInfrastructurePlan(plan.plan_id, plan.revision); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `infrastructure-plan-${plan.plan_id}.zip`; link.click() } catch (reason) { setError(errorText(reason)) } }

  return <PageShell view="infrastructure" title="Infrastructure plans">
    <p className="muted">Generate deterministic, reviewable configuration files from an owned architecture proposal and optional Phase 23 sizing result. This page never deploys or provisions SAP BTP resources.</p>
    <section className="card"><form className="form" onSubmit={submit} aria-label="Infrastructure plan inputs">
      <label className="field"><span className="field__label">Architecture proposal</span><select className="input" value={proposalId} onChange={(event) => { setProposalId(event.target.value); const item = proposals.find((proposal) => proposal.proposal_id === event.target.value); setRevision(item ? String(item.revision) : '') }}><option value="">Select an authorized proposal</option>{proposals.map((item) => <option key={item.proposal_id} value={item.proposal_id}>{item.title} · revision {item.revision}</option>)}</select></label>
      <div className="form-grid"><label className="field"><span className="field__label">Architecture revision</span><input className="input" value={revision} onChange={(event) => setRevision(event.target.value)} inputMode="numeric" /></label><label className="field"><span className="field__label">Sizing result (optional)</span><select className="input" value={sizingId} onChange={(event) => setSizingId(event.target.value)}><option value="">No sizing result</option>{sizing.filter((item) => !proposalId || item.architecture_proposal_id === proposalId).map((item) => <option key={item.result_id} value={item.result_id}>{item.scenario_id} · {item.status} · {item.result_id.slice(0, 8)}</option>)}</select></label><label className="field"><span className="field__label">Output format</span><select className="input" value={format} onChange={(event) => setFormat(event.target.value as typeof format)}><option value="cloudfoundry">Cloud Foundry manifest</option><option value="mta">MTA descriptor</option><option value="terraform">SAP/btp Terraform subset</option></select></label><label className="field"><span className="field__label">Application name (manifest)</span><input className="input" value={applicationName} onChange={(event) => setApplicationName(event.target.value)} placeholder="Required for Cloud Foundry" /></label></div>
      <label className="field"><span className="field__label">Safe environment variable names</span><input className="input" value={environmentVariables} onChange={(event) => setEnvironmentVariables(event.target.value)} placeholder="DATABASE_URL, LOG_LEVEL (values become placeholders)" /></label>
      {selectedProposal && <p className="field__hint">Selected runtime context: {selectedProposal.runtime_options.find((item) => item.suitability === 'SELECTED')?.name ?? 'not selected'}.</p>}
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      <div className="form__actions"><button type="submit" className="button button--primary" disabled={loading || !proposalId}>{loading ? 'Generating…' : 'Generate plan'}</button><button type="button" className="button button--ghost" onClick={() => navigateTo('/architecture')}>Return to Architecture Advisor</button></div>
    </form></section>
    {plan && <section className="card" aria-label="Infrastructure plan result"><div className="detail__header"><div><h2 className="card__title">Plan revision {plan.revision}</h2><p className="muted">{plan.output_format} · {plan.validation_status} · generator {plan.generator_version}</p></div><span className={`badge ${plan.validation_status === 'VALID' ? 'badge--ok' : 'badge--muted'}`}>{plan.validation_status}</span></div><p>{plan.impact.configuration_changes.join(' ')}</p><h3 className="section-title">Generated files</h3><div className="inventory-grid">{plan.artifacts.map((artifact) => <article className="card" key={artifact.filename}><h4>{artifact.filename}</h4><p className="muted">{artifact.validation_status} · {artifact.size_bytes} bytes · {artifact.sha256.slice(0, 12)}…</p><pre className="card" style={{ maxHeight: 320, overflow: 'auto' }}>{artifact.content}</pre><button type="button" className="button button--ghost" onClick={() => download(artifact.filename, artifact.content)}>Download file</button></article>)}</div><h3 className="section-title">Prerequisites and validation</h3><ul>{plan.missing_prerequisites.map((item) => <li key={item}><strong>Missing:</strong> {item}</li>)}{plan.unsupported_components.map((item) => <li key={item}><strong>Unsupported:</strong> {item}</li>)}{plan.validations.map((item, index) => <li key={`${item.rule_id}-${index}`}><strong>{item.severity}:</strong> {item.message}</li>)}</ul><h3 className="section-title">Expected static impact</h3><ul>{plan.impact.estimated_capacity.map((item) => <li key={item}>{item}</li>)}{plan.impact.quota_impact.map((item) => <li key={item}>{item}</li>)}{plan.impact.unsupported_operations.map((item) => <li key={item}>{item}</li>)}</ul><div className="form__actions"><button type="button" className="button button--primary" onClick={() => void validate()} disabled={loading}>Validate plan</button><button type="button" className="button button--ghost" onClick={() => void exportPackage()}>Export review package</button><input className="input" aria-label="Left revision" value={compare.left} onChange={(event) => setCompare({ ...compare, left: event.target.value })} /><input className="input" aria-label="Right revision" value={compare.right} onChange={(event) => setCompare({ ...compare, right: event.target.value })} /><button type="button" className="button button--ghost" onClick={() => void comparePlans()}>Compare revisions</button></div>{comparisonText && <pre className="card" aria-label="Revision comparison">{comparisonText}</pre>}<details><summary>Review report</summary><pre className="card">{plan.review_report}</pre></details></section>}
  </PageShell>
}
