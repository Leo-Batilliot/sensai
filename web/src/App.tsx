import { useEffect, useState } from 'react'
import './App.css'

type BackendStatus = 'checking' | 'up' | 'down'
type SideTab = 'timeline' | 'memory'

const BACKEND_LABELS: Record<BackendStatus, string> = {
  checking: 'Connexion…',
  up: 'Serveur connecté',
  down: 'Serveur injoignable',
}

function useBackendStatus(): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>('checking')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/health', { signal: controller.signal })
      .then((response) => setStatus(response.ok ? 'up' : 'down'))
      .catch(() => {
        if (!controller.signal.aborted) setStatus('down')
      })
    return () => controller.abort()
  }, [])

  return status
}

export default function App() {
  const backend = useBackendStatus()
  const [tab, setTab] = useState<SideTab>('timeline')

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
          <span className="chip">Modèle : —</span>
          <span className="chip">Serveurs MCP : —</span>
          <span className="chip">Contexte : —</span>
          <span className={`chip chip-${backend}`} role="status">
            {BACKEND_LABELS[backend]}
          </span>
        </div>
      </header>

      <main className="conversation" aria-label="Conversation">
        <div className="messages">
          <div className="empty-state">
            <h1>Que voulez-vous savoir sur vos données ?</h1>
            <p className="muted">
              Les réponses sont vérifiées contre la base. Rien ne quitte cette machine.
            </p>
          </div>
        </div>
        <form className="composer" onSubmit={(event) => event.preventDefault()}>
          <input
            type="text"
            aria-label="Question"
            placeholder="Posez une question sur vos données…"
            disabled
          />
          <button type="submit" disabled>
            Envoyer
          </button>
        </form>
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
