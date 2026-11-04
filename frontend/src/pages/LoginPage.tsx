import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { ApiError } from '../services/api'

export function LoginPage() {
  const { login, notice } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await login(email, password)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Sign-in failed. Please try again.')
      setPassword('')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="auth">
      <form className="auth__card" onSubmit={submit} noValidate>
        <h1 className="auth__title">BTP Operations Agent</h1>
        <p className="auth__subtitle">Sign in with your work account.</p>

        {notice && (
          <p className="banner banner--info" role="status">
            {notice}
          </p>
        )}
        {error && (
          <p className="banner banner--error" role="alert">
            {error}
          </p>
        )}

        <label className="field">
          <span className="field__label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
        </label>

        <div className="field">
          <label className="field__label" htmlFor="login-password">
            Password
          </label>
          <span className="input-group">
            <input
              id="login-password"
              className="input"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button
              type="button"
              className="button button--ghost input-group__button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              aria-pressed={showPassword}
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </span>
        </div>

        <button type="submit" className="button button--primary auth__submit" disabled={pending || !email || !password}>
          {pending ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="auth__hint">Accounts are created by an administrator.</p>
      </form>
    </main>
  )
}
