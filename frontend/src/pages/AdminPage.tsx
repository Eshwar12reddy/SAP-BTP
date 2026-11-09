import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { PageShell, ROLE_LABELS } from '../components/AppNav'
import { GrantTable } from '../components/GrantTable'
import { ApiError, api } from '../services/api'
import type { AdminUserDetail, AdminUserSummary, AuditEvent, BtpHierarchy, Grant, ManageableScopes, RoleName, ScopeType } from '../types/api'

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback)
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function AdminPage() {
  const { me, can } = useAuth()
  const [tab, setTab] = useState<'users' | 'audit' | 'btp'>('users')
  return (
    <PageShell view="admin" title="Administration">
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'users'} className="tabs__tab" onClick={() => setTab('users')}>
          Users
        </button>
        {can('audit:read') && (
          <button type="button" role="tab" aria-selected={tab === 'audit'} className="tabs__tab" onClick={() => setTab('audit')}>
            Audit log
          </button>
        )}
        {can('grants:manage') && (
          <button type="button" role="tab" aria-selected={tab === 'btp'} className="tabs__tab" onClick={() => setTab('btp')}>
            BTP scopes
          </button>
        )}
      </div>
      {tab === 'users' ? <UsersPanel currentUserId={me?.user.id ?? ''} /> : tab === 'audit' ? <AuditPanel /> : <BtpScopesPanel />}
    </PageShell>
  )
}

function BtpScopesPanel() {
  const [hierarchy, setHierarchy] = useState<BtpHierarchy | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [showSetup, setShowSetup] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      setHierarchy(await api.admin.btpHierarchy())
      setError(null)
    } catch (e) {
      setError(errorText(e, 'Could not load BTP scopes.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const changed = async (message: string) => {
    setNotice(message)
    await load()
  }

  return <section className="admin__scopes">
    {error && <p className="banner banner--error" role="alert">{error}</p>}
    {notice && <p className="banner banner--info" role="status">{notice}</p>}
    <section className="card">
      <div className="detail__header"><div><h2 className="card__title">Configured BTP scopes</h2><p className="muted">Register the non-secret account hierarchy used for authorization. This does not create accounts in SAP BTP or store credentials.</p></div><button type="button" className="button button--ghost" onClick={() => void load()} disabled={loading}>Refresh</button></div>
      {loading ? <p className="muted">Loading BTP scopes…</p> : hierarchy && hierarchy.accounts.length === 0 ? <div><p className="muted">No BTP hierarchy is configured. Users cannot receive Viewer, Operator, or Approver access until a scope exists.</p><button type="button" className="button button--primary" onClick={() => setShowSetup(true)}>Configure BTP scopes</button></div> : <ScopeTables hierarchy={hierarchy!} onChanged={changed} />}
    </section>
    {showSetup && <BtpScopeForms hierarchy={hierarchy ?? { accounts: [], environments: [] }} onChanged={changed} onClose={() => setShowSetup(false)} />}
    {hierarchy && hierarchy.accounts.length > 0 && <button type="button" className="button button--ghost" onClick={() => setShowSetup((value) => !value)}>{showSetup ? 'Hide scope forms' : 'Add or edit scopes'}</button>}
  </section>
}

function ScopeTables({ hierarchy, onChanged }: { hierarchy: BtpHierarchy; onChanged: (message: string) => Promise<void> }) {
  const [editingAccount, setEditingAccount] = useState<string | null>(null)
  const [editingEnvironment, setEditingEnvironment] = useState<string | null>(null)
  return <>
    <h3 className="section-title">Accounts and subaccounts</h3>
    <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Type</th><th>Parent</th><th>Actions</th></tr></thead><tbody>{hierarchy.accounts.map((account) => <tr key={account.id}><td>{account.name}</td><td>{account.account_type}</td><td>{hierarchy.accounts.find((parent) => parent.id === account.parent_account_id)?.name ?? '—'}</td><td><button type="button" className="button button--ghost" onClick={() => setEditingAccount(account.id)}>Edit</button>{editingAccount === account.id && <InlineAccountEdit account={account} onChanged={onChanged} onCancel={() => setEditingAccount(null)} />}</td></tr>)}</tbody></table></div>
    <h3 className="section-title">Environments</h3>
    <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Runtime</th><th>Subaccount</th><th>Actions</th></tr></thead><tbody>{hierarchy.environments.map((environment) => <tr key={environment.id}><td>{environment.name}</td><td>{environment.runtime_type}</td><td>{hierarchy.accounts.find((account) => account.id === environment.btp_account_id)?.name ?? '—'}</td><td><button type="button" className="button button--ghost" onClick={() => setEditingEnvironment(environment.id)}>Edit</button>{editingEnvironment === environment.id && <InlineEnvironmentEdit environment={environment} onChanged={onChanged} onCancel={() => setEditingEnvironment(null)} />}</td></tr>)}</tbody></table></div>
  </>
}

function BtpScopeForms({ hierarchy, onChanged, onClose }: { hierarchy: BtpHierarchy; onChanged: (message: string) => Promise<void>; onClose: () => void }) {
  const [accountType, setAccountType] = useState<'global_account' | 'subaccount'>('global_account')
  const [externalId, setExternalId] = useState('')
  const [name, setName] = useState('')
  const [region, setRegion] = useState('')
  const [parentId, setParentId] = useState('')
  const [environmentAccount, setEnvironmentAccount] = useState('')
  const [environmentName, setEnvironmentName] = useState('')
  const [runtime, setRuntime] = useState('cloudfoundry')
  const [runtimeId, setRuntimeId] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submitAccount = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(null); try { await api.admin.createBtpAccount({ external_account_id: externalId, name, account_type: accountType, region, ...(accountType === 'subaccount' ? { parent_account_id: parentId } : {}) }); setExternalId(''); setName(''); setRegion(''); setParentId(''); await onChanged('BTP account registered. No role was granted automatically.') } catch (e) { setError(errorText(e, 'The BTP account could not be registered.')) } finally { setPending(false) } }
  const submitEnvironment = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(null); try { await api.admin.createBtpEnvironment(environmentAccount, { name: environmentName, runtime_type: runtime, external_runtime_id: runtimeId }); setEnvironmentName(''); setRuntimeId(''); await onChanged('BTP environment registered. No role was granted automatically.') } catch (e) { setError(errorText(e, 'The BTP environment could not be registered.')) } finally { setPending(false) } }
  const globals = hierarchy.accounts.filter((account) => account.account_type === 'global_account')
  const subs = hierarchy.accounts.filter((account) => account.account_type === 'subaccount')
  return <section className="card"><div className="detail__header"><h2 className="card__title">Configure BTP scopes</h2><button type="button" className="button button--ghost" onClick={onClose}>Close</button></div>{error && <p className="banner banner--error" role="alert">{error}</p>}<form className="form" onSubmit={submitAccount} aria-label="Register BTP account"><h3 className="section-title">Register account or subaccount</h3><label className="field"><span className="field__label">Type</span><select className="input" value={accountType} onChange={(e) => { setAccountType(e.target.value as 'global_account' | 'subaccount'); setParentId('') }}><option value="global_account">Global account</option><option value="subaccount">Subaccount</option></select></label><label className="field"><span className="field__label">SAP external account ID</span><input className="input" value={externalId} onChange={(e) => setExternalId(e.target.value)} required /></label><label className="field"><span className="field__label">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="field"><span className="field__label">Region (optional)</span><input className="input" value={region} onChange={(e) => setRegion(e.target.value)} /></label>{accountType === 'subaccount' && <label className="field"><span className="field__label">Global account parent</span><select className="input" value={parentId} onChange={(e) => setParentId(e.target.value)} required><option value="">Select a global account…</option>{globals.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label>}<button type="submit" className="button button--primary" disabled={pending || (accountType === 'subaccount' && !parentId)}>{pending ? 'Saving…' : 'Register account'}</button></form><form className="form" onSubmit={submitEnvironment} aria-label="Register BTP environment"><h3 className="section-title">Register environment</h3><label className="field"><span className="field__label">Subaccount</span><select className="input" value={environmentAccount} onChange={(e) => setEnvironmentAccount(e.target.value)} required><option value="">Select a subaccount…</option>{subs.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label><label className="field"><span className="field__label">Environment name</span><input className="input" value={environmentName} onChange={(e) => setEnvironmentName(e.target.value)} required /></label><label className="field"><span className="field__label">Runtime</span><select className="input" value={runtime} onChange={(e) => setRuntime(e.target.value)}><option value="cloudfoundry">Cloud Foundry</option><option value="kyma">Kyma</option><option value="abap">ABAP</option><option value="neo">Neo</option></select></label><label className="field"><span className="field__label">External runtime ID (optional)</span><input className="input" value={runtimeId} onChange={(e) => setRuntimeId(e.target.value)} /></label><button type="submit" className="button button--primary" disabled={pending || !environmentAccount}>{pending ? 'Saving…' : 'Register environment'}</button></form></section>
}

function InlineAccountEdit({ account, onChanged, onCancel }: { account: BtpHierarchy['accounts'][number]; onChanged: (message: string) => Promise<void>; onCancel: () => void }) {
  const [name, setName] = useState(account.name)
  const [externalId, setExternalId] = useState(account.external_account_id)
  const [region, setRegion] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(null); try { await api.admin.updateBtpAccount(account.id, { external_account_id: externalId, name, region }); await onChanged('BTP account updated.'); onCancel() } catch (e) { setError(errorText(e, 'The BTP account could not be updated.')) } finally { setPending(false) } }
  return <form className="form" onSubmit={save} aria-label={`Edit ${account.name}`}><label className="field"><span className="field__label">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="field"><span className="field__label">External ID (blank keeps current)</span><input className="input" value={externalId} onChange={(e) => setExternalId(e.target.value)} /></label><label className="field"><span className="field__label">Region</span><input className="input" value={region} onChange={(e) => setRegion(e.target.value)} /></label>{error && <p className="banner banner--error" role="alert">{error}</p>}<button className="button button--primary" type="submit" disabled={pending}>Save</button><button className="button button--ghost" type="button" onClick={onCancel}>Cancel</button></form>
}

function InlineEnvironmentEdit({ environment, onChanged, onCancel }: { environment: BtpHierarchy['environments'][number]; onChanged: (message: string) => Promise<void>; onCancel: () => void }) {
  const [name, setName] = useState(environment.name)
  const [runtime, setRuntime] = useState(environment.runtime_type)
  const [externalId, setExternalId] = useState(environment.external_runtime_id ?? '')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(null); try { await api.admin.updateBtpEnvironment(environment.id, { name, runtime_type: runtime, external_runtime_id: externalId }); await onChanged('BTP environment updated.'); onCancel() } catch (e) { setError(errorText(e, 'The BTP environment could not be updated.')) } finally { setPending(false) } }
  return <form className="form" onSubmit={save} aria-label={`Edit ${environment.name}`}><label className="field"><span className="field__label">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label><label className="field"><span className="field__label">Runtime</span><select className="input" value={runtime} onChange={(e) => setRuntime(e.target.value)}><option value="cloudfoundry">Cloud Foundry</option><option value="kyma">Kyma</option><option value="abap">ABAP</option><option value="neo">Neo</option></select></label><label className="field"><span className="field__label">External runtime ID</span><input className="input" value={externalId} onChange={(e) => setExternalId(e.target.value)} /></label>{error && <p className="banner banner--error" role="alert">{error}</p>}<button className="button button--primary" type="submit" disabled={pending}>Save</button><button className="button button--ghost" type="button" onClick={onCancel}>Cancel</button></form>
}

// ---------- users ----------

function UsersPanel({ currentUserId }: { currentUserId: string }) {
  const [search, setSearch] = useState('')
  const [users, setUsers] = useState<AdminUserSummary[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [scopes, setScopes] = useState<ManageableScopes | null>(null)

  const load = useCallback(async (query: string) => {
    setStatus('loading')
    try {
      setUsers((await api.admin.listUsers(query)).items)
      setStatus('ready')
    } catch (e) {
      setError(errorText(e, 'Could not load users.'))
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => void load(search), 250)
    return () => clearTimeout(timer)
  }, [search, load])

  useEffect(() => {
    api.admin.scopes().then(setScopes).catch(() => setScopes(null))
  }, [])

  return (
    <div className="admin">
      <section className="admin__list card">
        <div className="admin__toolbar">
          <input
            className="input"
            type="search"
            placeholder="Search name or email"
            aria-label="Search users"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {scopes?.platform && (
            <button type="button" className="button button--primary" onClick={() => { setCreating(true); setSelected(null) }}>
              New user
            </button>
          )}
        </div>
        {status === 'error' && <p className="banner banner--error" role="alert">{error}</p>}
        {status === 'loading' && users.length === 0 && <p className="muted">Loading users…</p>}
        {status === 'ready' && users.length === 0 && <p className="muted">No users found.</p>}
        <ul className="admin__users" aria-label="Users">
          {users.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                className="admin__user"
                aria-current={u.id === selected ? 'true' : undefined}
                onClick={() => { setSelected(u.id); setCreating(false) }}
              >
                <span className="admin__user-name">
                  {u.display_name}
                  {!u.is_active && <span className="badge badge--muted">Disabled</span>}
                </span>
                <span className="muted">{u.email}</span>
                <span className="muted">{u.roles.map((r) => ROLE_LABELS[r]).join(', ') || 'No roles'}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="admin__detail">
        {creating ? (
          <CreateUserForm
            onCreated={(id) => {
              setCreating(false)
              setSelected(id)
              void load(search)
            }}
            onCancel={() => setCreating(false)}
          />
        ) : selected ? (
          <UserDetail
            key={selected}
            userId={selected}
            isSelf={selected === currentUserId}
            scopes={scopes}
            onChanged={() => void load(search)}
          />
        ) : (
          <div className="card muted">Select a user to manage their access.</div>
        )}
      </section>
    </div>
  )
}

function CreateUserForm({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (password.length < 12) return setError('The initial password must be at least 12 characters.')
    setPending(true)
    setError(null)
    try {
      const user = await api.admin.createUser({ email, display_name: name, password })
      onCreated(user.id)
    } catch (e) {
      setError(errorText(e, 'The user could not be created.'))
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="card form" onSubmit={submit}>
      <h2 className="card__title">New user</h2>
      <label className="field">
        <span className="field__label">Email</span>
        <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label className="field">
        <span className="field__label">Display name</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <div className="field">
        <label className="field__label" htmlFor="new-user-password">Initial password</label>
        <input
          id="new-user-password"
          className="input"
          type="password"
          autoComplete="new-password"
          aria-describedby="new-user-password-hint"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <span id="new-user-password-hint" className="field__hint">
          At least 12 characters. Share it securely; the user can change it in their profile.
        </span>
      </div>
      {error && <p className="banner banner--error" role="alert">{error}</p>}
      <div className="form__actions">
        <button type="submit" className="button button--primary" disabled={pending || !email || !name}>
          {pending ? 'Creating…' : 'Create user'}
        </button>
        <button type="button" className="button button--ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function UserDetail({
  userId,
  isSelf,
  scopes,
  onChanged,
}: {
  userId: string
  isSelf: boolean
  scopes: ManageableScopes | null
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<AdminUserDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDetail(await api.admin.getUser(userId))
    } catch (e) {
      setError(errorText(e, 'Could not load this user.'))
    }
  }, [userId])

  useEffect(() => {
    void load()
  }, [load])

  const act = async (key: string, action: () => Promise<string | void>) => {
    setBusy(key)
    setError(null)
    setMessage(null)
    try {
      const text = await action()
      if (text) setMessage(text)
      await load()
      onChanged()
    } catch (e) {
      setError(errorText(e, 'The change was not saved.'))
    } finally {
      setBusy(null)
    }
  }

  if (!detail) return <div className="card">{error ? <p className="banner banner--error" role="alert">{error}</p> : 'Loading…'}</div>
  const { user } = detail

  return (
    <div className="card">
      <div className="detail__header">
        <div>
          <h2 className="card__title">{user.display_name}</h2>
          <p className="muted">{user.email}</p>
        </div>
        <span className={`badge ${user.is_active ? 'badge--ok' : 'badge--muted'}`}>{user.is_active ? 'Active' : 'Disabled'}</span>
      </div>
      <p className="muted">
        {detail.active_sessions} active session{detail.active_sessions === 1 ? '' : 's'}
        {user.last_login_at && ` · last sign-in ${dateTime.format(new Date(user.last_login_at))}`}
      </p>

      {detail.manageable_account && !isSelf && (
        <div className="form__actions">
          <button
            type="button"
            className="button button--ghost"
            disabled={busy !== null}
            onClick={() => void act('status', async () => { await api.admin.setActive(user.id, !user.is_active) })}
          >
            {user.is_active ? 'Disable account' : 'Enable account'}
          </button>
          <button
            type="button"
            className="button button--ghost"
            disabled={busy !== null || detail.active_sessions === 0}
            onClick={() =>
              void act('sessions', async () => {
                const { sessions_revoked } = await api.admin.revokeSessions(user.id)
                return `${sessions_revoked} session${sessions_revoked === 1 ? '' : 's'} revoked.`
              })
            }
          >
            Revoke sessions
          </button>
        </div>
      )}
      {isSelf && <p className="muted">You cannot change your own access here.</p>}
      {message && <p className="banner banner--info" role="status">{message}</p>}
      {error && <p className="banner banner--error" role="alert">{error}</p>}

      <h3 className="section-title">Roles and scopes</h3>
      <GrantTable
        grants={detail.grants}
        busyId={busy}
        onRevoke={isSelf ? undefined : (g: Grant) => void act(g.id, async () => { await api.admin.revokeGrant(g.id) })}
      />
      {!isSelf && scopes && (
        <AssignGrantForm scopes={scopes} onAssign={(body) => act('assign', async () => { await api.admin.assignGrant(user.id, body) })} />
      )}
    </div>
  )
}

function AssignGrantForm({
  scopes,
  onAssign,
}: {
  scopes: ManageableScopes
  onAssign: (body: Parameters<typeof api.admin.assignGrant>[1]) => Promise<void>
}) {
  const roles: RoleName[] = [
    'viewer', 'operator', 'approver', 'knowledge_reader', 'knowledge_manager',
    ...(scopes.platform ? (['administrator'] as RoleName[]) : []),
  ]
  const [role, setRole] = useState<RoleName>('viewer')
  const [scope, setScope] = useState('')
  const [includeChildren, setIncludeChildren] = useState(false)
  const [expires, setExpires] = useState('')

  const options: { value: string; label: string; type: ScopeType }[] = [
    ...((role === 'administrator' || role === 'knowledge_reader' || role === 'knowledge_manager') && scopes.platform
      ? [{ value: 'platform:', label: role === 'administrator' ? 'Platform (all users)' : 'Platform (global knowledge)', type: 'platform' as ScopeType }]
      : []),
    ...scopes.accounts.map((a) => ({
      value: `${a.account_type}:${a.id}`,
      label: `${a.account_type === 'global_account' ? 'Global account' : 'Subaccount'} · ${a.name}`,
      type: a.account_type as ScopeType,
    })),
    ...(role === 'administrator'
      ? []
      : scopes.environments.map((e) => {
          const account = scopes.accounts.find((a) => a.id === e.btp_account_id)
          return {
            value: `environment:${e.id}`,
            label: `Environment · ${e.name} (${e.runtime_type})${account ? ` in ${account.name}` : ''}`,
            type: 'environment' as ScopeType,
          }
        })),
  ]
  const chosen = options.find((o) => o.value === scope)
  const canIncludeChildren = chosen?.type === 'global_account' || chosen?.type === 'subaccount'

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!chosen) return
    const [, id] = chosen.value.split(':')
    await onAssign({
      role,
      scope_type: chosen.type,
      scope_id: id || null,
      include_descendants: canIncludeChildren && includeChildren,
      // Date-only input: valid through the end of that day (UTC).
      expires_at: expires ? new Date(`${expires}T23:59:59Z`).toISOString() : null,
    })
    setScope('')
    setIncludeChildren(false)
    setExpires('')
  }

  return (
    <form className="assign" onSubmit={submit} aria-label="Assign role">
      <h3 className="section-title">Assign role</h3>
      <div className="assign__grid">
        <label className="field">
          <span className="field__label">Role</span>
          <select className="input" value={role} onChange={(e) => { setRole(e.target.value as RoleName); setScope('') }}>
            {roles.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r]}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Scope</span>
          <select className="input" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">Select a scope…</option>
            {options.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Expires (optional)</span>
          <input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
        </label>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={includeChildren} disabled={!canIncludeChildren} onChange={(e) => setIncludeChildren(e.target.checked)} />
        Include child subaccounts and environments
      </label>
      {options.length === 0 && <p className="muted">No BTP scopes are configured or delegated to you.</p>}
      <div>
        <button type="submit" className="button button--primary" disabled={!chosen}>
          Assign
        </button>
      </div>
    </form>
  )
}

// ---------- audit ----------

function AuditPanel() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.admin.auditEvents().then((page) => setEvents(page.items)).catch((e) => setError(errorText(e, 'Could not load audit events.')))
  }, [])
  if (error) return <p className="banner banner--error" role="alert">{error}</p>
  if (!events) return <p className="muted">Loading audit events…</p>
  if (events.length === 0) return <p className="muted">No audit events in your scope yet.</p>
  return (
    <div className="card table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Event</th>
            <th>Outcome</th>
            <th>Actor</th>
            <th>Target</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{dateTime.format(new Date(e.created_at))}</td>
              <td><code>{e.event_type}</code></td>
              <td><span className={`badge badge--${e.outcome === 'success' ? 'ok' : 'warn'}`}>{e.outcome}</span></td>
              <td className="muted">{e.actor_user_id?.slice(0, 8) ?? '—'}</td>
              <td className="muted">{e.target_user_id?.slice(0, 8) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
