import { useState, type FormEvent } from 'react'
import { PageShell } from '../components/AppNav'
import { navigateTo } from '../routes/router'
import { ApiError, api } from '../services/api'
import type { ArchitectureComparisonResponse, ArchitectureProposal } from '../types/api'

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : 'The architecture advisor is unavailable.'
}

function proposalText(proposal: ArchitectureProposal): string {
  return [proposal.title, proposal.overview, '', 'Components:', ...proposal.components.map((item) => `- ${item.name}: ${item.purpose}`), '', 'Connections:', ...proposal.connections.map((item) => `- ${item.source_component_id} -> ${item.target_component_id}: ${item.purpose}`), '', proposal.diagram.mermaid].join('\n')
}

export function ArchitectureAdvisorPage() {
  const [requirements, setRequirements] = useState('')
  const [runtime, setRuntime] = useState('')
  const [live, setLive] = useState(false)
  const [proposal, setProposal] = useState<ArchitectureProposal | null>(null)
  const [comparison, setComparison] = useState<ArchitectureComparisonResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [revising, setRevising] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const generate = async (event: FormEvent) => {
    event.preventDefault()
    if (!requirements.trim()) return
    setLoading(true); setError(null); setComparison(null)
    try {
      const text = runtime ? `${requirements.trim()} Preferred runtime: ${runtime}.` : requirements.trim()
      setProposal(await api.createArchitectureProposal({ request_text: text, include_live_evidence: live }))
    } catch (e) { setError(errorText(e)) } finally { setLoading(false) }
  }

  const revise = async () => {
    if (!proposal || !requirements.trim()) return
    setRevising(true); setError(null)
    try { setProposal(await api.reviseArchitectureProposal(proposal.proposal_id, { request_text: requirements.trim(), include_live_evidence: live })) } catch (e) { setError(errorText(e)) } finally { setRevising(false) }
  }

  const compare = async () => {
    if (!proposal) return
    try { setComparison(await api.compareArchitectureProposal(proposal)) } catch (e) { setError(errorText(e)) }
  }

  const validate = async () => {
    if (!proposal) return
    setValidating(true); setError(null)
    try { setProposal(await api.validateArchitectureProposal(proposal)) } catch (e) { setError(errorText(e)) } finally { setValidating(false) }
  }

  const copy = async () => { if (proposal) await navigator.clipboard.writeText(proposalText(proposal)) }
  const exportProposal = () => {
    if (!proposal) return
    const url = URL.createObjectURL(new Blob([proposalText(proposal)], { type: 'text/plain' }))
    const link = document.createElement('a'); link.href = url; link.download = `btp-architecture-${proposal.proposal_id}-r${proposal.revision}.txt`; link.click(); URL.revokeObjectURL(url)
  }

  return <PageShell view="architecture" title="Architecture advisor">
    <p className="muted">Turn explicit business and technical requirements into a reviewable SAP BTP architecture proposal. The advisor does not create accounts, provision services or deploy changes.</p>
    <section className="card"><form className="form" onSubmit={generate} aria-label="Architecture requirements">
      <label className="field"><span className="field__label">Project and application requirements</span><textarea className="input" rows={7} value={requirements} onChange={(event) => setRequirements(event.target.value)} placeholder="Example: Employee management application connected to SAP S/4HANA with secure login, approvals, a database and event-driven integration for about 1,000 employees." required /></label>
      <label className="field"><span className="field__label">Runtime preference</span><select className="input" value={runtime} onChange={(event) => setRuntime(event.target.value)}><option value="">Not specified</option><option value="cloudfoundry">Cloud Foundry</option><option value="kyma">Kyma</option><option value="abap">ABAP environment</option></select></label>
      <label className="field"><span className="field__label"><input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} /> Include authorized live catalog evidence</span><span className="field__hint">Provider visibility is not entitlement, quota or pricing evidence.</span></label>
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      <button type="submit" className="button button--primary" disabled={loading}>{loading ? 'Generating…' : 'Generate architecture'}</button>
    </form></section>
    {proposal && <ProposalView proposal={proposal} onRevise={() => void revise()} onValidate={() => void validate()} onCompare={() => void compare()} onCopy={() => void copy()} onExport={exportProposal} revising={revising} validating={validating} />}
    {comparison && <section className="card" aria-label="Architecture comparison"><h2 className="card__title">Alternative comparison</h2>{comparison.compared_alternatives.length === 0 ? <p className="muted">No technically relevant alternatives were generated.</p> : comparison.compared_alternatives.map((item) => <article key={item.alternative_id} className="card"><h3 className="section-title">{item.name}</h3><p>{item.change_from_primary}</p><p className="muted">Benefits: {item.benefits.join('; ') || 'Not specified'} · Drawbacks: {item.drawbacks.join('; ') || 'Not specified'}</p></article>)}</section>}
  </PageShell>
}

function ProposalView({ proposal, onRevise, onValidate, onCompare, onCopy, onExport, revising, validating }: { proposal: ArchitectureProposal; onRevise: () => void; onValidate: () => void; onCompare: () => void; onCopy: () => void; onExport: () => void; revising: boolean; validating: boolean }) {
  return <section className="card" aria-label="Architecture proposal"><div className="detail__header"><div><h2 className="card__title">{proposal.title}</h2><p className="muted">Revision {proposal.revision} · validation: {proposal.validation.valid ? 'valid' : 'review required'}</p></div><span className={`badge ${proposal.validation.valid ? 'badge--ok' : 'badge--muted'}`}>{proposal.validation.valid ? 'Validated' : 'Review'}</span></div><p>{proposal.overview}</p>
    <h3 className="section-title">Architecture diagram</h3><pre className="card" style={{ overflowX: 'auto' }}>{proposal.diagram.mermaid}</pre>
    <h3 className="section-title">Components and services</h3><div className="inventory-grid">{proposal.components.map((item) => <article className="card" key={item.component_id}><h4>{item.name}</h4><p>{item.purpose}</p><p className="muted">{item.component_type} · {item.verification_status}</p></article>)}</div>
    <h3 className="section-title">Environment layout</h3><p>{proposal.environments.map((item) => `${item.name}: ${item.account_structure}`).join(' · ')}</p>
    <h3 className="section-title">Availability and recovery</h3><p>{[proposal.requirements.availability_objectives, proposal.requirements.recovery_objectives].filter(Boolean).join(' · ') || 'Availability, RTO and RPO objectives require confirmation.'}</p>
    <h3 className="section-title">Security design</h3><ul>{proposal.security_controls.map((item) => <li key={item.control_id}><strong>{item.control}:</strong> {item.recommendation}</li>)}</ul>
    <h3 className="section-title">Risks and assumptions</h3><ul>{proposal.risks.map((item) => <li key={item.risk_id}><strong>{item.severity}:</strong> {item.risk} — {item.mitigation}</li>)}{proposal.assumptions.map((item) => <li key={item.assumption_id}><strong>Assumption:</strong> {item.statement}</li>)}</ul>
    <h3 className="section-title">Official evidence</h3><ul>{proposal.evidence.filter((item) => item.evidence_type === 'OFFICIAL_DOCUMENTATION').map((item) => <li key={item.evidence_id}><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> ({item.verification_status})</li>)}</ul>
    <div className="form__actions"><button type="button" className="button button--primary" onClick={onRevise} disabled={revising}>{revising ? 'Revising…' : 'Revise proposal'}</button><button type="button" className="button button--ghost" onClick={onValidate} disabled={validating}>{validating ? 'Validating…' : 'Validate proposal'}</button><button type="button" className="button button--ghost" onClick={onCompare}>Compare alternatives</button><button type="button" className="button button--ghost" onClick={() => navigateTo(`/sizing?proposal_id=${encodeURIComponent(proposal.proposal_id)}`)}>Calculate sizing</button><button type="button" className="button button--ghost" onClick={() => navigateTo(`/infrastructure-plans?proposal_id=${encodeURIComponent(proposal.proposal_id)}`)}>Generate infrastructure plan</button><button type="button" className="button button--ghost" onClick={onCopy}>Copy proposal</button><button type="button" className="button button--ghost" onClick={onExport}>Export text</button></div>
  </section>
}
