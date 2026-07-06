import { useGroupAdmin } from './useGroupAdmin'
import { LoginScreen } from './components/LoginScreen'
import { PageHeader } from './components/PageHeader'
import { ControlPanel } from './components/ControlPanel'
import { MemberGrid } from './components/MemberGrid'
import { Footer } from './components/Footer'

function App() {
  const app = useGroupAdmin()

  if (app.sessionLoading) {
    return <main className="loading">Loading...</main>
  }
  if (!app.session) {
    return <LoginScreen onLogin={app.login} />
  }

  const filterTerm = app.mode === 'filter' ? app.query.trim() : ''
  const showRoster = !app.error && !app.loading && app.group !== null
  const justAddedIds = new Set(app.recentlyAdded.map((m) => m.id))

  return (
    <>
      <PageHeader memberName={app.session.displayName} />
      <main className="page">
        <ControlPanel
          groups={app.groups}
          selectedId={app.selectedId}
          onSelect={app.selectGroup}
          people={app.counts.people}
          leaders={app.counts.leaders}
          mode={app.mode}
          query={app.query}
          onModeChange={app.setMode}
          onQueryChange={app.setQuery}
          onSearch={app.search}
          add={app.add}
          onConfirmAdd={app.confirmAdd}
        />

        {/* Active filter is always rendered: when idle it's an empty flex box whose top margin
            reserves the gap above the grid; when filtering it carries the
            filter summary. */}
        <div className="activefilter">
          {filterTerm && (
            <>
              <span>
                Filtering by <b>&ldquo;{filterTerm}&rdquo;</b> &middot; {app.visibleMembers.length}{' '}
                of {app.counts.people}
              </span>
              <button type="button" className="activefilter__clear" onClick={() => app.setQuery('')}>
                Show everyone
              </button>
            </>
          )}
        </div>

        {app.error && <p className="status status--bad">{app.error}</p>}
        {app.loading && <p className="status">Loading group...</p>}

        {showRoster && (
          <MemberGrid
            members={app.visibleMembers}
            removingIds={app.removingIds}
            justAddedIds={justAddedIds}
            selfId={app.selfId}
            groupName={app.group?.name ?? ''}
            filterTerm={filterTerm}
            onStartRemove={app.startRemove}
            onUndoRemove={app.undoRemove}
          />
        )}
      </main>
      <Footer onLogout={app.logout} />
    </>
  )
}

export default App
