import { useState } from 'react'
import './App.css'
import './chat/chat.css'
import { ChatInput } from './chat/ChatInput'
import { EmptyState } from './chat/EmptyState'
import { MessageList } from './chat/MessageList'
import type { ConnectionStatus } from './session/reducer'
import { useSession } from './session/useSession'

type SideTab = 'timeline' | 'memory'

const CONNECTION: Record<ConnectionStatus, { label: string; tone: string }> = {
  connecting: { label: 'Connexion…', tone: '' },
  open: { label: 'Connecté', tone: 'chip-up' },
  reconnecting: { label: 'Reconnexion…', tone: 'chip-down' },
  replaced: { label: 'Ouverte ailleurs', tone: 'chip-down' },
}

const numbers = new Intl.NumberFormat('fr-FR')

// Layout of the main screen (docs/keynote/ui/EcranPrincipal). Still to come: SQL and
// results (WEB-06), HITL modal (WEB-07), judge badge (WEB-09), status bar (WEB-10),
// memory panel (WEB-11).
export default function App() {
  const session = useSession()
  const { state } = session
  const [tab, setTab] = useState<SideTab>('timeline')
  const connection = CONNECTION[state.connection]
  const servers = state.session?.servers ?? []
  const usage = state.lastUsage

  return (
    <div className="app">
      <header className="statusbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            S
          </span>
          <strong>SENSAI</strong>
          <span className="muted">Nordvent Distribution</span>
        </div>
        <div className="statusbar-items">
          <span className="chip">Modèle : {state.session?.model ?? '—'}</span>
          <span className="chip">
            Serveurs MCP : {servers.filter((server) => server.status === 'up').length}/
            {servers.length}
          </span>
          <span className="chip">
            Contexte :{' '}
            {usage === null
              ? '—'
              : `${numbers.format(usage.ctx_used)} / ${numbers.format(usage.ctx_max)}`}
          </span>
          <span className={`chip ${connection.tone}`} role="status">
            {connection.label}
          </span>
        </div>
      </header>

      <main className="conversation" aria-label="Conversation">
        <div className="banners">
          {state.incompatible && (
            <div className="banner bad" role="alert">
              <p>Version de l'interface incompatible : rechargez la page.</p>
            </div>
          )}
          {state.connection === 'replaced' && (
            <div className="banner bad" role="alert">
              <p>Cette conversation est ouverte dans un autre onglet.</p>
            </div>
          )}
          {state.connection === 'reconnecting' && (
            <div className="banner" role="status">
              <p>Connexion au serveur perdue, reconnexion en cours…</p>
            </div>
          )}
          {state.notices.map((notice) => (
            <div key={notice.id} className="banner" role="alert">
              <p>{notice.message}</p>
              <button type="button" onClick={() => session.dismissNotice(notice.id)}>
                Fermer
              </button>
            </div>
          ))}
        </div>

        {state.turns.length === 0 ? (
          <EmptyState
            disabled={!session.connected || session.busy}
            onAsk={session.sendMessage}
          />
        ) : (
          <MessageList turns={state.turns} />
        )}

        <ChatInput
          busy={session.busy}
          connected={session.connected}
          onSend={session.sendMessage}
          onCancel={session.cancel}
        />
      </main>

      <aside className="sidepanel" aria-label="Étapes de l'agent et mémoire">
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            id="tab-timeline"
            aria-controls="panel-side"
            aria-selected={tab === 'timeline'}
            onClick={() => setTab('timeline')}
          >
            Timeline
          </button>
          <button
            type="button"
            role="tab"
            id="tab-memory"
            aria-controls="panel-side"
            aria-selected={tab === 'memory'}
            onClick={() => setTab('memory')}
          >
            Mémoire
          </button>
        </div>
        <div
          className="tab-panel"
          role="tabpanel"
          id="panel-side"
          aria-labelledby={tab === 'timeline' ? 'tab-timeline' : 'tab-memory'}
        >
          <p className="muted">
            {tab === 'timeline'
              ? "Les étapes de l'agent s'afficheront ici pendant qu'il travaille."
              : "Le glossaire, les entités et les faits retenus par l'agent s'afficheront ici."}
          </p>
        </div>
      </aside>
    </div>
  )
}
