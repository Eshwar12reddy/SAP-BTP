import { useEffect, useState, type FormEvent } from 'react'
import { PageShell } from '../components/AppNav'
import { useAuth } from '../auth/AuthProvider'
import { ApiError, api } from '../services/api'
import type {
  AvailableEnvironment,
  BtpConnection,
  CloudFoundryApplication,
  ApplicationDiagnosis,
  CloudFoundryOrganization,
  CloudFoundryServiceInstance,
  CloudFoundryProcess,
  CloudFoundryProcessStats,
  CloudFoundryServiceBinding,
  CloudFoundryApplicationEvent,
  CloudFoundrySpace,
  CloudFoundryServiceOffering,
  CloudFoundryServicePlan,
  CloudFoundryQuota,
  InventoryStatus,
  HealthOverview,
  ConnectionValidationResult,
} from '../types/api'

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

const RESULT_LABELS: Record<ConnectionValidationResult, string> = {
  not_validated: 'Not validated',
  valid_configuration: 'Configuration valid',
  validated: 'Live validation passed',
  credentials_unavailable: 'Credentials unavailable',
  authentication_failed: 'Authentication failed',
  endpoint_unreachable: 'Endpoint unreachable',
  tls_failure: 'TLS or transport failure',
  unsupported_runtime: 'Unsupported runtime',
  unsupported_authentication: 'Unsupported authentication',
  connection_disabled: 'Connection disabled',
  invalid_configuration: 'Invalid configuration',
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.code === 'btp_no_active_environment') return 'Select an active BTP environment to inspect live inventory.'
  return error instanceof ApiError ? error.message : fallback
}

export function BtpConnectionsPage() {
  const { can } = useAuth()
  const [connections, setConnections] = useState<BtpConnection[]>([])
  const [environments, setEnvironments] = useState<AvailableEnvironment[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState<BtpConnection | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [connectionPage, environmentPage, active] = await Promise.all([
        api.listBtpConnections(),
        api.availableBtpEnvironments(),
        api.activeBtpEnvironment(),
      ])
      setConnections(connectionPage.items)
      setEnvironments(environmentPage.items)
      setActiveId(active.connection?.id ?? null)
      setError(null)
    } catch (e) {
      setError(errorText(e, 'Could not load BTP connection settings.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    setMessage(null)
    try {
      await action()
      await load()
    } catch (e) {
      setError(errorText(e, 'The BTP connection change was not saved.'))
    } finally {
      setBusy(null)
    }
  }

  const validate = (connection: BtpConnection) => run(`validate:${connection.id}`, async () => {
    const result = await api.validateBtpConnection(connection.id)
    setMessage(RESULT_LABELS[result.result])
  })

  const disable = (connection: BtpConnection) => {
    if (!window.confirm(`Disable ${connection.display_name}?`)) return
    void run(`disable:${connection.id}`, async () => {
      await api.disableBtpConnection(connection.id)
      setMessage('Connection disabled.')
    })
  }

  const select = (connection: BtpConnection) => run(`select:${connection.id}`, async () => {
    await api.selectBtpEnvironment(connection.id)
    setActiveId(connection.id)
    setMessage(`${connection.environment_name} is now the active environment.`)
  })

  return (
    <PageShell view="btp" title="BTP connections">
      <p className="muted">
        Manage non-secret connection settings for environments you are authorized to use. Credential values remain in the backend environment.
      </p>
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      {message && <p className="banner banner--info" role="status">{message}</p>}

      <section className="card">
        <div className="detail__header">
          <div>
            <h2 className="card__title">Active environment</h2>
            <p className="muted">The selection is re-authorized on every backend request.</p>
          </div>
          {activeId && <span className="badge badge--ok">Selected</span>}
        </div>
        {!activeId && <p className="muted">No active BTP environment selected.</p>}
        {activeId && <p>{connections.find((c) => c.id === activeId)?.environment_name ?? 'Selected environment'}</p>}
      </section>

      <section className="card">
        <div className="detail__header">
          <h2 className="card__title">Connections</h2>
          {can('btp:connections:create') && (
            <button type="button" className="button button--primary" onClick={() => setShowAdd((value) => !value)}>
              {showAdd ? 'Cancel' : 'Add connection'}
            </button>
          )}
        </div>
        {showAdd && <ConnectionForm environments={environments} onSaved={() => { setShowAdd(false); void load() }} />}
        {loading ? <p className="muted">Loading connections…</p> : connections.length === 0 ? (
          <p className="muted">No connections are configured for your accessible environments.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Environment</th><th>Runtime</th><th>Status</th><th>Validation</th><th>Actions</th></tr></thead>
              <tbody>
                {connections.map((connection) => (
                  <tr key={connection.id}>
                    <td>{connection.display_name}</td>
                    <td>{connection.environment_name}</td>
                    <td>{connection.runtime_type}</td>
                    <td><span className={`badge ${connection.status === 'enabled' ? 'badge--ok' : 'badge--muted'}`}>{activeId === connection.id ? 'Selected' : connection.status}</span></td>
                    <td>
                      {RESULT_LABELS[connection.last_validation_result]}
                      {connection.last_validation_at && <div className="muted">{dateTime.format(new Date(connection.last_validation_at))}</div>}
                    </td>
                    <td>
                      <div className="form__actions">
                        {can('btp:environment:select') && connection.enabled && <button type="button" className="button button--ghost" disabled={busy !== null} onClick={() => void select(connection)}>{activeId === connection.id ? 'Selected' : 'Select'}</button>}
                        {can('btp:connections:validate') && <button type="button" className="button button--ghost" disabled={busy !== null} onClick={() => void validate(connection)}>Validate</button>}
                        {can('btp:connections:update') && <button type="button" className="button button--ghost" disabled={busy !== null} onClick={() => setEditing(connection)}>Edit</button>}
                        {can('btp:connections:update') && connection.enabled && <button type="button" className="button button--ghost" disabled={busy !== null} onClick={() => disable(connection)}>Disable</button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <CloudFoundryInventory active={Boolean(activeId)} />
      {editing && <EditConnectionForm connection={editing} onSaved={() => { setEditing(null); void load() }} onCancel={() => setEditing(null)} />}
    </PageShell>
  )
}

function CloudFoundryInventory({ active }: { active: boolean }) {
  const [organizations, setOrganizations] = useState<CloudFoundryOrganization[]>([])
  const [spaces, setSpaces] = useState<CloudFoundrySpace[]>([])
  const [applications, setApplications] = useState<CloudFoundryApplication[]>([])
  const [services, setServices] = useState<CloudFoundryServiceInstance[]>([])
  const [offerings, setOfferings] = useState<CloudFoundryServiceOffering[]>([])
  const [plans, setPlans] = useState<CloudFoundryServicePlan[]>([])
  const [quotas, setQuotas] = useState<CloudFoundryQuota[]>([])
  const [destinationStatus, setDestinationStatus] = useState<InventoryStatus | null>(null)
  const [entitlementStatus, setEntitlementStatus] = useState<InventoryStatus | null>(null)
  const [healthOverview, setHealthOverview] = useState<HealthOverview | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [diagnosis, setDiagnosis] = useState<ApplicationDiagnosis | null>(null)
  const [selectedApplication, setSelectedApplication] = useState<CloudFoundryApplication | null>(null)
  const [processes, setProcesses] = useState<CloudFoundryProcess[]>([])
  const [processStats, setProcessStats] = useState<CloudFoundryProcessStats[]>([])
  const [bindings, setBindings] = useState<CloudFoundryServiceBinding[]>([])
  const [events, setEvents] = useState<CloudFoundryApplicationEvent[]>([])
  const [inspecting, setInspecting] = useState(false)
  const [diagnosing, setDiagnosing] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    if (!active) return
    setLoading(true)
    setError(null)
    try {
      const [organizationPage, spacePage, applicationPage, servicePage, offeringPage, planPage, orgQuotaPage, spaceQuotaPage, destinations, entitlements] = await Promise.all([
        api.listRuntimeOrganizations(),
        api.listRuntimeSpaces(),
        api.listRuntimeApplications(),
        api.listRuntimeServiceInstances(),
        api.listRuntimeServiceOfferings(),
        api.listRuntimeServicePlans(),
        api.listRuntimeOrganizationQuotas(),
        api.listRuntimeSpaceQuotas(),
        api.getRuntimeDestinationStatus(),
        api.getRuntimeEntitlementStatus(),
      ])
      setOrganizations(organizationPage.items)
      setSpaces(spacePage.items)
      setApplications(applicationPage.items)
      setServices(servicePage.items)
      setOfferings(offeringPage.items)
      setPlans(planPage.items)
      setQuotas([...orgQuotaPage.items, ...spaceQuotaPage.items])
      setDestinationStatus(destinations)
      setEntitlementStatus(entitlements)
      setHealthError(null)
      void api.getRuntimeHealthOverview().then(setHealthOverview).catch(() => setHealthError('Health overview is unavailable or only partially configured.'))
      setDiagnosis(null)
      setSelectedApplication(null)
      setProcesses([])
      setProcessStats([])
      setBindings([])
      setEvents([])
    } catch (e) {
      setError(errorText(e, 'Live Cloud Foundry inventory is unavailable.'))
    } finally {
      setLoading(false)
    }
  }

  const inspectApplication = async (application: CloudFoundryApplication) => {
    setInspecting(true)
    setError(null)
    try {
      const [processPage, bindingPage, eventPage] = await Promise.all([
        api.listRuntimeProcesses(application.id),
        api.listRuntimeServiceBindings(application.id),
        api.listRuntimeApplicationEvents(application.id),
      ])
      const stats = await Promise.all(processPage.items.map((process) => api.getRuntimeProcessStats(process.id)))
      setSelectedApplication(application)
      setProcesses(processPage.items)
      setProcessStats(stats)
      setBindings(bindingPage.items)
      setEvents(eventPage.items)
    } catch (e) {
      setError(errorText(e, 'Application details are unavailable.'))
    } finally {
      setInspecting(false)
    }
  }

  useEffect(() => {
    if (!active) {
      setOrganizations([])
      setSpaces([])
      setApplications([])
      setServices([])
      setOfferings([])
      setPlans([])
      setQuotas([])
      setDestinationStatus(null)
      setEntitlementStatus(null)
      setHealthOverview(null)
      setHealthError(null)
      setDiagnosis(null)
      return
    }
    void refresh()
  }, [active])

  return (
    <section className="card" aria-label="Cloud Foundry inventory">
      <div className="detail__header">
        <div>
          <h2 className="card__title">Live Cloud Foundry inventory</h2>
          <p className="muted">Read-only observations from the selected environment. Credentials and service-binding values are never shown.</p>
        </div>
        <button type="button" className="button button--ghost" onClick={() => void refresh()} disabled={!active || loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      {!active && <p className="muted">Select an active Cloud Foundry connection to inspect authorized inventory.</p>}
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      {active && !loading && !error && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Organizations</th><th>Spaces</th><th>Applications</th><th>Service instances</th><th>Offerings</th><th>Plans</th><th>Quotas</th></tr></thead>
            <tbody><tr><td>{organizations.length}</td><td>{spaces.length}</td><td>{applications.length}</td><td>{services.length}</td><td>{offerings.length}</td><td>{plans.length}</td><td>{quotas.length}</td></tr></tbody>
          </table>
          <div className="inventory-grid">
            <div><h3 className="section-title">Organizations</h3>{organizations.length === 0 ? <p className="muted">None returned.</p> : organizations.map((organization) => <p key={organization.id}>{organization.display_name}</p>)}</div>
            <div><h3 className="section-title">Spaces</h3>{spaces.length === 0 ? <p className="muted">None returned.</p> : spaces.map((space) => <p key={space.id}>{space.display_name}</p>)}</div>
            <div><h3 className="section-title">Service instances</h3>{services.length === 0 ? <p className="muted">None returned.</p> : services.map((service) => <p key={service.id}>{service.display_name}</p>)}</div>
            <div><h3 className="section-title">Provider catalog</h3><p>{offerings.length} offerings, {plans.length} plans</p><p className="muted">Entitlement is UNKNOWN unless a separate SAP entitlement check succeeds.</p></div>
            <div><h3 className="section-title">Quotas</h3>{quotas.length === 0 ? <p className="muted">None returned.</p> : quotas.map((quota) => <p key={quota.id}>{quota.display_name} ({quota.quota_type})</p>)}</div>
            <div><h3 className="section-title">SAP service status</h3><p>Destinations: <strong>{destinationStatus?.retrieval_status ?? 'unknown'}</strong></p><p>Entitlements: <strong>{entitlementStatus?.retrieval_status ?? 'unknown'}</strong></p></div>
            <div><h3 className="section-title">Health &amp; quotas</h3>{healthError ? <p className="muted">{healthError}</p> : healthOverview ? <><p>Status: <strong>{healthOverview.diagnostic_status}</strong>; applications checked: {healthOverview.applications_checked}</p><p>Findings: {healthOverview.findings.length}; last checked: {dateTime.format(new Date(healthOverview.generated_at))}</p></> : <p className="muted">Not yet retrieved.</p>}</div>
          </div>
          {applications.length > 0 && (
            <div className="form__actions" style={{ marginTop: '1rem', flexWrap: 'wrap' }}>
              {applications.slice(0, 10).map((application) => <span key={application.id}>
                <span className="muted">{application.display_name || application.id}</span>{' '}
                <button type="button" className="button button--ghost" disabled={inspecting} onClick={() => void inspectApplication(application)}>{inspecting && selectedApplication?.id === application.id ? 'Loading…' : 'Details'}</button>
                <button
                  type="button"
                  className="button button--ghost"
                  disabled={diagnosing !== null}
                  onClick={() => {
                    setDiagnosing(application.id)
                    void api.diagnoseRuntimeApplication(application.id)
                      .then(setDiagnosis)
                      .catch((e) => setError(errorText(e, 'Live diagnosis is unavailable.')))
                      .finally(() => setDiagnosing(null))
                  }}
                >
                  {diagnosing === application.id ? 'Diagnosing…' : 'Diagnose'}
                </button>
              </span>)}
              {applications.length > 10 ? '…' : ''}
            </div>
          )}
          {diagnosis && <DiagnosisView diagnosis={diagnosis} />}
          {selectedApplication && <ApplicationDetails application={selectedApplication} processes={processes} processStats={processStats} bindings={bindings} events={events} />}
        </div>
      )}
    </section>
  )
}

function DiagnosisView({ diagnosis }: { diagnosis: ApplicationDiagnosis }) {
  return (
    <div className="card" style={{ marginTop: '1rem' }} aria-label="Application diagnosis">
      <h3 className="section-title">Operational diagnosis: {diagnosis.summary.display_name}</h3>
      <p>
        State: <strong>{diagnosis.summary.state ?? 'unknown'}</strong>; requested instances: {diagnosis.summary.requested_instances ?? 'unknown'}; running instances: {diagnosis.summary.running_instances ?? 'unknown'}.
      </p>
      {diagnosis.findings.map((finding) => (
        <div key={finding.finding_id} className="card" style={{ marginTop: '0.75rem' }}>
          <p><span className="badge badge--muted">{finding.severity}</span>{' '}<strong>{finding.confidence}</strong> — {finding.observation}</p>
          {finding.possible_causes.length > 0 && <p className="muted">Possible causes: {finding.possible_causes.join('; ')}</p>}
          {finding.recommended_next_steps.length > 0 && <p className="muted">Next steps: {finding.recommended_next_steps.join('; ')}</p>}
          {finding.evidence_references.length > 0 && <p className="muted">Evidence: {finding.evidence_references.join(', ')}</p>}
        </div>
      ))}
      {diagnosis.missing_information.length > 0 && <p className="muted">Missing information: {diagnosis.missing_information.join(' ')}</p>}
      <p className="muted">{diagnosis.root_cause_statement}</p>
      {diagnosis.ai_explanation && <p><strong>AI explanation:</strong> {diagnosis.ai_explanation}</p>}
    </div>
  )
}

function ApplicationDetails({ application, processes, processStats, bindings, events }: { application: CloudFoundryApplication; processes: CloudFoundryProcess[]; processStats: CloudFoundryProcessStats[]; bindings: CloudFoundryServiceBinding[]; events: CloudFoundryApplicationEvent[] }) {
  return <div className="card" style={{ marginTop: '1rem' }} aria-label="Application inventory details">
    <h3 className="section-title">{application.display_name} details</h3>
    <p className="muted">State: {application.lifecycle_state ?? 'unknown'} · lifecycle: {application.lifecycle ?? 'unknown'}</p>
    <h4>Processes and stats</h4>
    {processes.length === 0 ? <p className="muted">No processes returned.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Type</th><th>State</th><th>Instances</th><th>Observed stats</th></tr></thead><tbody>{processes.map((process, index) => <tr key={process.id}><td>{process.process_type}</td><td>{process.state ?? 'unknown'}</td><td>{process.instances ?? 'unknown'}</td><td>{processStats[index]?.items.map((stat) => `${stat.index ?? '-'}: ${stat.state ?? 'unknown'}`).join(', ') || 'No instance stats'}</td></tr>)}</tbody></table></div>}
    <h4>Service bindings</h4>
    {bindings.length === 0 ? <p className="muted">No service bindings returned. Binding credentials are intentionally not displayed.</p> : <ul>{bindings.map((binding) => <li key={binding.id}>{binding.binding_type} · service {binding.service_instance_id ?? 'unknown'}</li>)}</ul>}
    <h4>Recent events</h4>
    {events.length === 0 ? <p className="muted">No application events returned.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Type</th><th>Previous</th><th>Current</th><th>Instances</th></tr></thead><tbody>{events.slice(0, 20).map((event) => <tr key={event.id}><td>{event.event_type ?? 'unknown'}</td><td>{event.previous_state ?? 'unknown'}</td><td>{event.current_state ?? 'unknown'}</td><td>{event.instance_count ?? 'unknown'}</td></tr>)}</tbody></table></div>}
  </div>
}

function ConnectionForm({ environments, onSaved }: { environments: AvailableEnvironment[]; onSaved: () => void }) {
  const [displayName, setDisplayName] = useState('')
  const [environmentId, setEnvironmentId] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [reference, setReference] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setError(null)
    try {
      await api.createBtpConnection({ display_name: displayName, btp_environment_id: environmentId, api_endpoint: endpoint, credential_reference: reference, authentication_method: 'bearer_token' })
      onSaved()
    } catch (e) { setError(errorText(e, 'Could not create the connection.')) } finally { setPending(false) }
  }
  return <form className="form btp-form" onSubmit={submit} aria-label="Add BTP connection">
    <h3 className="section-title">Add connection</h3>
    <label className="field"><span className="field__label">Display name</span><input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required /></label>
    <label className="field"><span className="field__label">Environment</span><select className="input" value={environmentId} onChange={(e) => setEnvironmentId(e.target.value)} required><option value="">Select an environment…</option>{environments.filter((e) => e.status === 'not_configured').map((e) => <option key={e.id} value={e.id}>{e.name} ({e.runtime_type})</option>)}</select></label>
    <label className="field"><span className="field__label">HTTPS API endpoint</span><input className="input" type="url" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://api.example" required /></label>
    <label className="field"><span className="field__label">Credential reference</span><input className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="env://cf-local" required /><span className="field__hint">Opaque server-side reference; secret values are never sent here.</span></label>
    {error && <p className="banner banner--error" role="alert">{error}</p>}
    <button type="submit" className="button button--primary" disabled={pending || !environmentId}>{pending ? 'Saving…' : 'Save connection'}</button>
  </form>
}

function EditConnectionForm({ connection, onSaved, onCancel }: { connection: BtpConnection; onSaved: () => void; onCancel: () => void }) {
  const [name, setName] = useState(connection.display_name)
  const [endpoint, setEndpoint] = useState(connection.api_endpoint)
  const [reference, setReference] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setError(null)
    try {
      await api.updateBtpConnection(connection.id, { revision: connection.revision, display_name: name, api_endpoint: endpoint, ...(reference ? { credential_reference: reference } : {}) })
      onSaved()
    } catch (e) { setError(errorText(e, 'Could not update the connection.')) } finally { setPending(false) }
  }
  return <section className="card"><form className="form btp-form" onSubmit={submit} aria-label="Edit BTP connection"><h2 className="card__title">Edit {connection.display_name}</h2><label className="field"><span className="field__label">Display name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="field"><span className="field__label">HTTPS API endpoint</span><input className="input" type="url" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} required /></label><label className="field"><span className="field__label">Replace credential reference (optional)</span><input className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Leave blank to keep the current reference" /></label>{error && <p className="banner banner--error" role="alert">{error}</p>}<div className="form__actions"><button type="submit" className="button button--primary" disabled={pending}>{pending ? 'Saving…' : 'Save changes'}</button><button type="button" className="button button--ghost" onClick={onCancel}>Cancel</button></div></form></section>
}
