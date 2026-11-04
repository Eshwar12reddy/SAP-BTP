import { AuthProvider, useAuth } from './auth/AuthProvider'
import { PageShell } from './components/AppNav'
import { AdminPage } from './pages/AdminPage'
import { ChatPage } from './pages/ChatPage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { useView } from './routes/router'

export default function App() {
  return (
    <AuthProvider>
      <Root />
    </AuthProvider>
  )
}

function Root() {
  const { status } = useAuth()
  if (status === 'loading') {
    return (
      <div className="splash" role="status">
        Loading…
      </div>
    )
  }
  // Every route is protected; the URL is kept so the user lands back where they were.
  if (status === 'anonymous') return <LoginPage />
  return <AuthenticatedApp />
}

function AuthenticatedApp() {
  const view = useView()
  const { can } = useAuth()

  if (view === 'profile') return <ProfilePage />
  if (view === 'admin') {
    return can('users:read') ? (
      <AdminPage />
    ) : (
      <PageShell view="admin" title="Administration">
        <p className="banner banner--error" role="alert">
          You do not have permission to manage users.
        </p>
      </PageShell>
    )
  }
  if (!can('chat:use')) {
    return (
      <PageShell view="chat" title="Chat">
        <p className="card muted">
          Your roles do not include chat access. Administrators manage users and access; ask a platform administrator
          for a Viewer role on a BTP scope to use the assistant.
        </p>
      </PageShell>
    )
  }
  return <ChatPage />
}
