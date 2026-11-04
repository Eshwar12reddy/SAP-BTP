import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { PageShell } from '../components/AppNav'
import { GrantTable } from '../components/GrantTable'
import { ApiError, api } from '../services/api'

const MIN_PASSWORD = 12
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function ProfilePage() {
  const { me, logoutAll } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [signingOutAll, setSigningOutAll] = useState(false)

  if (!me) return null

  const clientProblem =
    next && next.length < MIN_PASSWORD
      ? `The new password must be at least ${MIN_PASSWORD} characters.`
      : confirm && next !== confirm
        ? 'The new passwords do not match.'
        : null

  const changePassword = async (event: FormEvent) => {
    event.preventDefault()
    if (pending || clientProblem || !current || !next || next !== confirm) return
    setPending(true)
    setResult(null)
    try {
      const { other_sessions_revoked } = await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setResult({
        ok: true,
        text: `Password changed. ${other_sessions_revoked} other session${other_sessions_revoked === 1 ? '' : 's'} signed out.`,
      })
    } catch (e) {
      setResult({ ok: false, text: e instanceof ApiError ? e.message : 'The password could not be changed.' })
    } finally {
      setPending(false)
    }
  }

  const signOutEverywhere = async () => {
    if (!window.confirm('Sign out on all devices, including this one?')) return
    setSigningOutAll(true)
    try {
      await logoutAll()
    } catch (e) {
      setSigningOutAll(false)
      setResult({ ok: false, text: e instanceof ApiError ? e.message : 'Could not sign out everywhere.' })
    }
  }

  return (
    <PageShell view="profile" title="Profile">
      <section className="card">
        <h2 className="card__title">Account</h2>
        <dl className="details">
          <dt>Name</dt>
          <dd>{me.user.display_name}</dd>
          <dt>Email</dt>
          <dd>{me.user.email}</dd>
          <dt>Last sign-in</dt>
          <dd>{me.user.last_login_at ? dateTime.format(new Date(me.user.last_login_at)) : '—'}</dd>
        </dl>
      </section>

      <section className="card">
        <h2 className="card__title">Roles and scopes</h2>
        <GrantTable grants={me.grants} emptyText="No roles assigned yet. Ask an administrator for access." />
      </section>

      <section className="card">
        <h2 className="card__title">Change password</h2>
        <form className="form" onSubmit={changePassword}>
          <label className="field">
            <span className="field__label">Current password</span>
            <input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </label>
          <div className="field">
            <label className="field__label" htmlFor="new-password">New password</label>
            <input
              id="new-password"
              className="input"
              type="password"
              autoComplete="new-password"
              aria-describedby="new-password-hint"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
            <span id="new-password-hint" className="field__hint">
              At least {MIN_PASSWORD} characters. Other devices will be signed out.
            </span>
          </div>
          <label className="field">
            <span className="field__label">Confirm new password</span>
            <input className="input" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
          {(clientProblem || result) && (
            <p className={`banner ${clientProblem || !result?.ok ? 'banner--error' : 'banner--info'}`} role={clientProblem || !result?.ok ? 'alert' : 'status'}>
              {clientProblem ?? result?.text}
            </p>
          )}
          <div>
            <button type="submit" className="button button--primary" disabled={pending || !!clientProblem || !current || !next || next !== confirm}>
              {pending ? 'Saving…' : 'Change password'}
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2 className="card__title">Sessions</h2>
        <p className="muted">Sign out of every browser and device where you are signed in.</p>
        <button type="button" className="button button--ghost" onClick={() => void signOutEverywhere()} disabled={signingOutAll}>
          {signingOutAll ? 'Signing out…' : 'Sign out on all devices'}
        </button>
      </section>
    </PageShell>
  )
}
