import { useState } from 'react'
import './App.css'
import './chat/chat.css'
import './hitl/hitl.css'
import './judge/judge.css'
import './memory/memory.css'
import './question/question.css'
import './results/results.css'
import './sql/sql.css'
import './statusbar/statusbar.css'
import './timeline/timeline.css'
import { ChatInput } from './chat/ChatInput'
import { EmptyState } from './chat/EmptyState'
import { MessageList } from './chat/MessageList'
import { ConfirmationModal } from './hitl/ConfirmationModal'
import { pendingConfirmation } from './hitl/confirmation'
import { useOutcomeToast } from './hitl/useOutcomeToast'
import { MemoryPanel } from './memory/MemoryPanel'
import { memoryVersion, recentChanges } from './memory/memory'
import { useMemory } from './memory/useMemory'
import { useSession } from './session/useSession'
import { StatusBar } from './statusbar/StatusBar'
import { Timeline } from './timeline/Timeline'

type SideTab = 'timeline' | 'memory'

// Layout of the main screen (docs/keynote/ui/EcranPrincipal).
export default function App() {
  const session = useSession()
  const { state } = session
  const [tab, setTab] = useState<SideTab>('timeline')
  // Turn shown in the timeline: the one picked from a message, otherwise the last one
  const [pinnedTurnId, setPinnedTurnId] = useState<string | null>(null)
  const confirmation = pendingConfirmation(state.turns)
  const { toast, dismiss: dismissToast } = useOutcomeToast(state.turns)
  const pinned = state.turns.findIndex((turn) => turn.id === pinnedTurnId)
  const timelineIndex = pinned !== -1 ? pinned : state.turns.length - 1
  const memory = useMemory(session.connected, memoryVersion(state.turns))
  const canAct = session.connected && !session.busy

  const ask = (content: string) => {
    setPinnedTurnId(null) // the timeline follows the new turn
    session.sendMessage(content)
  }

  const showSteps = (turnId: string) => {
    setPinnedTurnId(turnId)
    setTab('timeline')
  }

  return (
    <div className="app">
      <StatusBar
        state={state}
        connected={session.connected}
        onSetModel={session.setModel}
        onSetPersona={session.setPersona}
      />

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
            onAsk={ask}
          />
        ) : (
          <MessageList
            turns={state.turns}
            personaMarks={state.personaMarks}
            connected={session.connected}
            refusalKey={state.nextNoticeId}
            onAnswer={session.answer}
            canAsk={session.connected && !session.busy}
            onAsk={ask}
            onShowSteps={showSteps}
            onShowMemory={() => setTab('memory')}
          />
        )}

        {toast !== null && (
          <div className={`toast tone-${toast.tone}`} role="status">
            <p>
              <strong>{toast.title}</strong> {toast.detail}
            </p>
            <button type="button" onClick={dismissToast} aria-label="Fermer la notification">
              ×
            </button>
          </div>
        )}

        <ChatInput
          busy={session.busy}
          connected={session.connected}
          onSend={ask}
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
          {tab === 'timeline' ? (
            <Timeline turn={state.turns[timelineIndex] ?? null} number={timelineIndex + 1} />
          ) : (
            <MemoryPanel
              memory={memory}
              recent={recentChanges(state.turns)}
              canAct={canAct}
              onAction={session.uiAction}
            />
          )}
        </div>
      </aside>

      {confirmation !== null && (
        <ConfirmationModal
          key={confirmation.action_id}
          confirmation={confirmation}
          connected={session.connected}
          onAnswer={(decision, comment) => session.confirm(confirmation.action_id, decision, comment)}
        />
      )}
    </div>
  )
}
