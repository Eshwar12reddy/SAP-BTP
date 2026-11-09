import { useState } from 'react'
import { PageShell } from '../components/AppNav'
import { ApiError, api } from '../services/api'
import type { ServiceComparisonResponse, ServiceRecommendation, ServiceRecommendationResponse } from '../types/api'

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : 'The recommendation service is unavailable.'
}

export function ServiceRecommendationsPage() {
  const [requestText, setRequestText] = useState('')
  const [includeLive, setIncludeLive] = useState(false)
  const [result, setResult] = useState<ServiceRecommendationResponse | null>(null)
  const [comparison, setComparison] = useState<ServiceComparisonResponse | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [compareLoading, setCompareLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!requestText.trim()) return
    setLoading(true); setError(null); setComparison(null); setSelected([])
    try {
      setResult(await api.recommendServices({ request_text: requestText.trim(), include_live_evidence: includeLive }))
    } catch (e) { setError(errorText(e)) } finally { setLoading(false) }
  }

  const compare = async () => {
    if (selected.length < 2) return
    setCompareLoading(true); setError(null)
    try { setComparison(await api.compareServices(selected, requestText)) } catch (e) { setError(errorText(e)) } finally { setCompareLoading(false) }
  }

  const toggle = (item: ServiceRecommendation) => {
    setSelected((current) => current.includes(item.recommendation_id.replace(/^rec-/, ''))
      ? current.filter((key) => key !== item.recommendation_id.replace(/^rec-/, ''))
      : [...current, item.recommendation_id.replace(/^rec-/, '')].slice(0, 4))
  }

  return (
    <PageShell view="recommendations" title="Service recommendations">
      <p className="muted">Describe the business outcome and technical constraints. Results use a verified reference catalog; account evidence is shown separately when requested.</p>
      <section className="card">
        <form className="form" onSubmit={submit} aria-label="Service recommendation request">
          <label className="field"><span className="field__label">Requirements</span><textarea className="input" rows={6} value={requestText} onChange={(event) => setRequestText(event.target.value)} placeholder="Example: Build an employee management application integrated with SAP S/4HANA. It needs secure identity, relational persistence and asynchronous events." required /></label>
          <label className="field"><span className="field__label"><input type="checkbox" checked={includeLive} onChange={(event) => setIncludeLive(event.target.checked)} /> Include authorized live provider catalog evidence</span><span className="field__hint">This does not prove entitlement, quota or pricing. It requires an authorized active environment.</span></label>
          {error && <p className="banner banner--error" role="alert">{error}</p>}
          <button type="submit" className="button button--primary" disabled={loading}>{loading ? 'Analyzing…' : 'Recommend services'}</button>
        </form>
      </section>

      {result && <RecommendationResults result={result} selected={selected} onToggle={toggle} onCompare={() => void compare()} compareLoading={compareLoading} />}
      {comparison && <Comparison result={comparison} />}
    </PageShell>
  )
}

function RecommendationResults({ result, selected, onToggle, onCompare, compareLoading }: { result: ServiceRecommendationResponse; selected: string[]; onToggle: (item: ServiceRecommendation) => void; onCompare: () => void; compareLoading: boolean }) {
  return <section className="card" aria-label="Service recommendation results">
    <div className="detail__header"><div><h2 className="card__title">Recommendations</h2><p className="muted">Catalog {result.catalog_version}; suitability and account availability are separate judgments.</p></div><button type="button" className="button button--ghost" disabled={selected.length < 2 || compareLoading} onClick={onCompare}>{compareLoading ? 'Comparing…' : `Compare selected (${selected.length})`}</button></div>
    {result.analysis.missing_critical_information.length > 0 && <p className="banner banner--info">To improve confidence: {result.analysis.missing_critical_information.join('; ')}.</p>}
    <p className="muted">Live catalog status: {result.live_catalog.retrieval_status}. Entitlement is never inferred from provider visibility.</p>
    {result.no_suitable_options ? <p className="muted">No suitable documented options were found for the supplied requirements.</p> : <div className="inventory-grid">{result.recommendations.map((item) => <RecommendationCard key={item.recommendation_id} item={item} checked={selected.includes(item.recommendation_id.replace(/^rec-/, ''))} onToggle={() => onToggle(item)} />)}</div>}
  </section>
}

function RecommendationCard({ item, checked, onToggle }: { item: ServiceRecommendation; checked: boolean; onToggle: () => void }) {
  return <article className="card"><div className="detail__header"><h3 className="section-title">{item.service_name}</h3><span className={`badge ${item.eligibility_status === 'SUPPORTED' ? 'badge--ok' : 'badge--muted'}`}>{item.eligibility_status}</span></div><p>{item.suitability_explanation}</p><p className="muted">Runtime: {item.runtime_status} · Region: {item.region_status} · Entitlement: {item.entitlement_status} · Quota: {item.quota_status}</p>{item.prerequisites.length > 0 && <p><strong>Prerequisites:</strong> {item.prerequisites.join('; ')}</p>}{item.relevant_alternatives.length > 0 && <p className="muted"><strong>Alternatives:</strong> {item.relevant_alternatives.join(', ')}</p>}<div className="form__actions"><label className="field__label"><input type="checkbox" checked={checked} onChange={onToggle} /> Compare</label>{item.evidence_references.filter((e) => e.source_type === 'DOCUMENTATION').map((evidence) => <a key={evidence.reference} className="button button--ghost" href={evidence.reference} target="_blank" rel="noreferrer">Official documentation</a>)}</div></article>
}

function Comparison({ result }: { result: ServiceComparisonResponse }) {
  return <section className="card" aria-label="Service comparison"><h2 className="card__title">Comparison</h2>{result.rows.map((row) => <div key={row.service_key} className="card" style={{ marginTop: '0.75rem' }}><h3 className="section-title">{row.service_name}</h3><p>{row.capability_fit}. {row.meaningful_difference}</p><p className="muted">Runtime: {row.runtime_status} · Region: {row.region_status} · Entitlement: {row.entitlement_status}</p></div>)}<p className="muted">Comparison does not include pricing because comparable verified price evidence was not supplied.</p></section>
}
