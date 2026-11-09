import { useEffect, useState, type FormEvent } from 'react'
import { PageShell } from '../components/AppNav'
import { useAuth } from '../auth/AuthProvider'
import { ApiError, api } from '../services/api'
import type { KnowledgeSearchResponse, KnowledgeSource, KnowledgeVersion, MyScopes } from '../types/api'

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function KnowledgePage() {
  const { can } = useAuth()
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [scopes, setScopes] = useState<MyScopes['btp_read']>({ accounts: [], environments: [] })
  const [selected, setSelected] = useState<KnowledgeSource | null>(null)
  const [versions, setVersions] = useState<KnowledgeVersion[]>([])
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState<KnowledgeSearchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [page, scopeData] = await Promise.all([api.listKnowledgeSources(), api.myScopes()])
      setSources(page.items)
      setScopes(scopeData.btp_read)
      setError(null)
    } catch (e) {
      setError(errorText(e, 'Knowledge sources are unavailable.'))
    } finally {
      setLoading(false)
    }
  }

  const select = async (source: KnowledgeSource) => {
    setSelected(source)
    try {
      const [fresh, page] = await Promise.all([api.getKnowledgeSource(source.id), api.listKnowledgeVersions(source.id)])
      setSelected(fresh)
      setVersions(page.items)
      setError(null)
    } catch (e) {
      setError(errorText(e, 'Could not load the selected source.'))
    }
  }

  useEffect(() => { void load() }, [])

  const runSearch = async (event: FormEvent) => {
    event.preventDefault()
    if (query.trim().length < 2) return
    setNotice(null)
    try {
      setSearch(await api.searchKnowledge(query.trim()))
      setError(null)
    } catch (e) {
      setError(errorText(e, 'Knowledge search is unavailable.'))
    }
  }

  const afterChange = async (message: string) => {
    setNotice(message)
    const page = await api.listKnowledgeSources()
    setSources(page.items)
    if (selected) {
      const fresh = page.items.find((source) => source.id === selected.id)
      if (fresh) await select(fresh)
    }
  }

  return (
    <PageShell view="knowledge" title="Knowledge">
      <p className="muted">Search authorized documentation and manage versioned sources. Uploaded files stay inside the backend storage boundary.</p>
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      {notice && <p className="banner banner--info" role="status">{notice}</p>}

      <section className="card">
        <h2 className="card__title">Search knowledge</h2>
        <form className="form" onSubmit={runSearch} aria-label="Search knowledge">
          <label className="field"><span className="field__label">Question or keywords</span><input className="input" value={query} onChange={(e) => setQuery(e.target.value)} minLength={2} maxLength={1000} placeholder="Search authorized documentation" /></label>
          <button className="button button--primary" type="submit" disabled={query.trim().length < 2}>Search</button>
        </form>
        {search && <SearchResults result={search} />}
      </section>

      {can('knowledge:create') && <UploadSourceForm scopes={scopes} onSaved={async (message) => { await afterChange(message) }} />}

      <section className="card">
        <div className="detail__header"><div><h2 className="card__title">Sources</h2><p className="muted">Only sources within your current knowledge grants are listed.</p></div><button className="button button--ghost" type="button" onClick={() => void load()} disabled={loading}>Refresh</button></div>
        {loading ? <p className="muted">Loading sources…</p> : sources.length === 0 ? <p className="muted">No knowledge sources are available.</p> : (
          <div className="table-wrap"><table className="table"><thead><tr><th>Title</th><th>Scope</th><th>Active version</th><th>Updated</th><th /></tr></thead><tbody>
            {sources.map((source) => <tr key={source.id}><td><strong>{source.title}</strong>{source.description && <div className="muted">{source.description}</div>}</td><td>{scopeLabel(source)}</td><td>v{source.active_version.version_number} · {formatBytes(source.active_version.size_bytes)}</td><td>{new Date(source.updated_at).toLocaleString()}</td><td><button className="button button--ghost" type="button" onClick={() => void select(source)}>Details</button></td></tr>)}
          </tbody></table></div>
        )}
      </section>

      {selected && <SourceDetails source={selected} versions={versions} canUpdate={can('knowledge:update')} canRemove={can('knowledge:remove')} onChanged={async (message) => { await afterChange(message) }} onClose={() => setSelected(null)} />}
    </PageShell>
  )
}

function scopeLabel(source: KnowledgeSource): string {
  if (source.scope_type === 'global') return 'Global'
  return source.scope_type === 'btp_account' ? 'BTP account' : 'BTP environment'
}

function SearchResults({ result }: { result: KnowledgeSearchResponse }) {
  return <div style={{ marginTop: '1rem' }}><p className="muted">Mode: {result.retrieval_mode}; index: {result.indexing_status}. {result.items.length} result(s).</p>{result.items.length === 0 ? <p className="muted">No matching evidence was found.</p> : result.items.map((item) => <article className="card" key={item.citation.citation_id}><h3 className="section-title">{item.citation.source_title}</h3><p>{item.excerpt}</p><p className="muted">Version {item.citation.version_number}{item.citation.section_heading ? ` · ${item.citation.section_heading}` : ''}</p><a className="link-button" href={item.citation.download_url} target="_blank" rel="noreferrer">Open source file</a></article>)}</div>
}

function UploadSourceForm({ scopes, onSaved }: { scopes: MyScopes['btp_read']; onSaved: (message: string) => Promise<void> }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [scope, setScope] = useState('global')
  const [scopeId, setScopeId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!file) return
    setPending(true); setError(null)
    try { await api.createKnowledgeSource({ title, description, scope_type: scope as 'global' | 'btp_account' | 'btp_environment', scope_id: scope === 'global' ? undefined : scopeId, file }); setTitle(''); setDescription(''); setFile(null); await onSaved('Knowledge source uploaded and validated.') }
    catch (e) { setError(errorText(e, 'The source could not be uploaded.')) }
    finally { setPending(false) }
  }
  const options = scope === 'btp_account' ? scopes.accounts : scopes.environments
  return <section className="card"><h2 className="card__title">Add a source</h2><form className="form" onSubmit={submit} aria-label="Upload knowledge source"><label className="field"><span className="field__label">Title</span><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} /></label><label className="field"><span className="field__label">Description</span><textarea className="input" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} rows={3} /></label><label className="field"><span className="field__label">Scope</span><select className="input" value={scope} onChange={(e) => { setScope(e.target.value); setScopeId('') }}><option value="global">Global</option><option value="btp_account">BTP account</option><option value="btp_environment">BTP environment</option></select></label>{scope !== 'global' && <label className="field"><span className="field__label">Scope target</span><select className="input" value={scopeId} onChange={(e) => setScopeId(e.target.value)} required><option value="">Select a target…</option>{options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}<label className="field" htmlFor="knowledge-document"><span className="field__label">Document</span><input id="knowledge-document" className="input" type="file" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required /><span className="field__hint">Accepted formats: PDF, text, Markdown. The backend validates and indexes the file.</span></label>{error && <p className="banner banner--error" role="alert">{error}</p>}<button className="button button--primary" type="submit" disabled={pending || !file || (scope !== 'global' && !scopeId)}>{pending ? 'Uploading…' : 'Upload source'}</button></form></section>
}

function SourceDetails({ source, versions, canUpdate, canRemove, onChanged, onClose }: { source: KnowledgeSource; versions: KnowledgeVersion[]; canUpdate: boolean; canRemove: boolean; onChanged: (message: string) => Promise<void>; onClose: () => void }) {
  const [title, setTitle] = useState(source.title)
  const [description, setDescription] = useState(source.description ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = async (action: () => Promise<void>) => { setBusy(true); setError(null); try { await action() } catch (e) { setError(errorText(e, 'The source change could not be completed.')) } finally { setBusy(false) } }
  return <section className="card" aria-label="Knowledge source details"><div className="detail__header"><div><h2 className="card__title">{source.title}</h2><p className="muted">{scopeLabel(source)} · revision {source.revision}</p></div><button className="button button--ghost" type="button" onClick={onClose}>Close</button></div>{error && <p className="banner banner--error" role="alert">{error}</p>}{canUpdate && <form className="form" onSubmit={(event) => { event.preventDefault(); void run(async () => { await api.updateKnowledgeSource(source.id, { revision: source.revision, title, description }); await onChanged('Source metadata updated.') }) }}><label className="field"><span className="field__label">Title</span><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required /></label><label className="field"><span className="field__label">Description</span><textarea className="input" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} /></label><button className="button button--primary" type="submit" disabled={busy}>Save metadata</button></form>}<div className="card"><h3 className="section-title">Versions</h3><div className="table-wrap"><table className="table"><thead><tr><th>Version</th><th>File</th><th>Status</th><th>Actions</th></tr></thead><tbody>{versions.map((version) => <tr key={version.id}><td>v{version.version_number}{version.is_active && <span className="badge badge--ok"> Active</span>}</td><td>{version.original_filename}<div className="muted">{formatBytes(version.size_bytes)}</div></td><td>{version.validation_status}</td><td><button className="button button--ghost" type="button" onClick={() => void run(async () => { const blob = await api.downloadKnowledgeVersion(source.id, version.id); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = version.original_filename; anchor.click(); URL.revokeObjectURL(url) })}>Download</button></td></tr>)}</tbody></table></div>{canUpdate && <form className="form" onSubmit={(event) => { event.preventDefault(); if (!file) return; void run(async () => { await api.uploadKnowledgeVersion(source.id, file); await onChanged('New source version uploaded.') }) }}><label className="field"><span className="field__label">Upload replacement</span><input className="input" type="file" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label><button className="button button--ghost" type="submit" disabled={busy || !file}>Upload new version</button></form>}</div>{canUpdate && <button className="button button--ghost" type="button" disabled={busy} onClick={() => void run(async () => { const result = await api.indexKnowledgeSource(source.id); await onChanged(`Index status: ${result.status}.`) })}>Re-index active version</button>}{canRemove && <button className="button button--ghost" type="button" disabled={busy} onClick={() => { const reason = window.prompt('Reason for removing this source:'); if (reason) void run(async () => { await api.removeKnowledgeSource(source.id, reason); onClose(); await onChanged('Source removed.') }) }}>Remove source</button>}</section>
}
