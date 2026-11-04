import { useEffect, useMemo, useState } from 'react'
import { api } from '../services/api'
import type { Conversation, MyScopes } from '../types/api'

const STORAGE_KEY = 'btp.chat.context'

export interface BtpContextState {
  scopes: MyScopes['btp_read'] | null
  value: string
  setValue: (value: string) => void
  /** Payload for creating a conversation in the selected context. */
  selectedContext: { btp_account_id?: string; btp_environment_id?: string }
}

function readStored(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

/** The account/environment the next conversation is about; options come from the
 * server's view of what the user may currently read. */
export function useBtpContext(): BtpContextState {
  const [scopes, setScopes] = useState<MyScopes['btp_read'] | null>(null)
  const [value, setValueState] = useState(readStored)

  useEffect(() => {
    let cancelled = false
    api
      .myScopes()
      .then((s) => !cancelled && setScopes(s.btp_read))
      .catch(() => !cancelled && setScopes({ accounts: [], environments: [] }))
    return () => {
      cancelled = true
    }
  }, [])

  const setValue = (next: string) => {
    setValueState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // A remembered selection is only a convenience.
    }
  }

  const selectedContext = useMemo(() => {
    const [kind, id] = value.split(':')
    if (!scopes || !id) return {}
    // A stored choice that is no longer authorized is silently dropped.
    if (kind === 'account' && scopes.accounts.some((a) => a.id === id)) return { btp_account_id: id }
    if (kind === 'environment' && scopes.environments.some((e) => e.id === id)) return { btp_environment_id: id }
    return {}
  }, [value, scopes])

  return { scopes, value, setValue, selectedContext }
}

export function contextLabel(scopes: MyScopes['btp_read'] | null, conversation: Conversation): string | null {
  if (!scopes) return null
  if (conversation.btp_account_id) {
    return scopes.accounts.find((a) => a.id === conversation.btp_account_id)?.name ?? null
  }
  if (conversation.btp_environment_id) {
    return scopes.environments.find((e) => e.id === conversation.btp_environment_id)?.name ?? null
  }
  return null
}

export function ContextSelector({ context }: { context: BtpContextState }) {
  const { scopes, setValue, selectedContext } = context
  if (!scopes || (scopes.accounts.length === 0 && scopes.environments.length === 0)) return null
  const current = selectedContext.btp_account_id
    ? `account:${selectedContext.btp_account_id}`
    : selectedContext.btp_environment_id
      ? `environment:${selectedContext.btp_environment_id}`
      : ''
  const accountName = (id: string | null) => scopes.accounts.find((a) => a.id === id)?.name

  return (
    <label className="context-select">
      <span className="visually-hidden">BTP context</span>
      <select className="input input--compact" value={current} onChange={(e) => setValue(e.target.value)} aria-label="BTP context">
        <option value="">No BTP context</option>
        {scopes.accounts.length > 0 && (
          <optgroup label="Accounts">
            {scopes.accounts.map((a) => (
              <option key={a.id} value={`account:${a.id}`}>
                {a.name} ({a.account_type === 'global_account' ? 'global account' : 'subaccount'})
              </option>
            ))}
          </optgroup>
        )}
        {scopes.environments.length > 0 && (
          <optgroup label="Environments">
            {scopes.environments.map((e) => (
              <option key={e.id} value={`environment:${e.id}`}>
                {e.name} · {e.runtime_type}
                {accountName(e.btp_account_id) ? ` (${accountName(e.btp_account_id)})` : ''}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  )
}
