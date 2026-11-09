import { AuthProvider, useAuth } from './auth/AuthProvider'
import { PageShell } from './components/AppNav'
import { AdminPage } from './pages/AdminPage'
import { BtpConnectionsPage } from './pages/BtpConnectionsPage'
import { ChatPage } from './pages/ChatPage'
import { LoginPage } from './pages/LoginPage'
import { ProfilePage } from './pages/ProfilePage'
import { KnowledgePage } from './pages/KnowledgePage'
import { ServiceRecommendationsPage } from './pages/ServiceRecommendationsPage'
import { ArchitectureAdvisorPage } from './pages/ArchitectureAdvisorPage'
import { SizingPage } from './pages/SizingPage'
import { InfrastructurePlansPage } from './pages/InfrastructurePlansPage'
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
  const { can, me } = useAuth()

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
  if (view === 'btp') {
    return can('btp:connections:read') ? (
      <BtpConnectionsPage />
    ) : (
      <PageShell view="btp" title="BTP connections">
        <p className="banner banner--error" role="alert">
          You do not have permission to view BTP connections.
        </p>
      </PageShell>
    )
  }
  if (view === 'knowledge') {
    return can('knowledge:read') ? (
      <KnowledgePage />
    ) : (
      <PageShell view="knowledge" title="Knowledge">
        <p className="banner banner--error" role="alert">You do not have permission to view knowledge sources.</p>
      </PageShell>
    )
  }
  if (view === 'recommendations') {
    return can('chat:use') ? <ServiceRecommendationsPage /> : <PageShell view="recommendations" title="Service recommendations"><p className="banner banner--error" role="alert">You do not have permission to use recommendations.</p></PageShell>
  }
  if (view === 'architecture') {
    return can('chat:use') ? <ArchitectureAdvisorPage /> : <PageShell view="architecture" title="Architecture advisor"><p className="banner banner--error" role="alert">You do not have permission to use the architecture advisor.</p></PageShell>
  }
  if (view === 'sizing') {
    return can('chat:use') ? <SizingPage /> : <PageShell view="sizing" title="Sizing calculator"><p className="banner banner--error" role="alert">You do not have permission to use the sizing calculator.</p></PageShell>
  }
  if (view === 'infrastructure') {
    return can('chat:use') ? <InfrastructurePlansPage /> : <PageShell view="infrastructure" title="Infrastructure plans"><p className="banner banner--error" role="alert">You do not have permission to use infrastructure plans.</p></PageShell>
  }
  if (!can('chat:use')) {
    return (
      <PageShell view="chat" title="Chat">
        {me?.grants.length === 0 ? (
          <section className="card" aria-label="Access not assigned">
            <h2 className="card__title">Access not assigned</h2>
            <p className="muted">
              Your account is active, but no role or BTP scope has been assigned yet. Ask an authorized administrator
              to assign Viewer access at the appropriate BTP scope. Chat access will appear after your next request or page refresh.
            </p>
          </section>
        ) : (
          <p className="card muted">
            Your roles do not include chat access. Administrators manage users and access; ask a platform administrator
            for a Viewer role on a BTP scope to use the assistant.
          </p>
        )}
      </PageShell>
    )
  }
  return <ChatPage />
}
