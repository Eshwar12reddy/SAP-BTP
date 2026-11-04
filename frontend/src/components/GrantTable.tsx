import type { Grant } from '../types/api'
import { ROLE_LABELS } from './AppNav'

const SCOPE_LABELS: Record<string, string> = {
  platform: 'Platform',
  global_account: 'Global account',
  subaccount: 'Subaccount',
  environment: 'Environment',
}

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' })

export function GrantTable({
  grants,
  onRevoke,
  busyId,
  emptyText = 'No roles assigned.',
}: {
  grants: Grant[]
  onRevoke?: (grant: Grant) => void
  busyId?: string | null
  emptyText?: string
}) {
  if (grants.length === 0) return <p className="muted">{emptyText}</p>
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Role</th>
          <th>Scope</th>
          <th>Includes children</th>
          <th>Expires</th>
          {onRevoke && <th aria-label="Actions" />}
        </tr>
      </thead>
      <tbody>
        {grants.map((g) => {
          const expired = g.expires_at !== null && new Date(g.expires_at) <= new Date()
          return (
            <tr key={g.id}>
              <td>{ROLE_LABELS[g.role] ?? g.role}</td>
              <td>
                <span className="muted">{SCOPE_LABELS[g.scope_type]}</span>
                {g.scope_type !== 'platform' && <> · {g.scope_name ?? 'Unknown'}</>}
              </td>
              <td>{g.include_descendants ? 'Yes' : 'No'}</td>
              <td>{g.expires_at ? `${dateFormat.format(new Date(g.expires_at))}${expired ? ' (expired)' : ''}` : 'Never'}</td>
              {onRevoke && (
                <td className="table__actions">
                  <button
                    type="button"
                    className="button button--ghost button--small"
                    onClick={() => onRevoke(g)}
                    disabled={busyId === g.id}
                  >
                    Revoke
                  </button>
                </td>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
