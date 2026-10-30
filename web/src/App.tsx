import { useState } from 'react'
import './App.css'
import './chat/chat.css'
import './hitl/hitl.css'
import './judge/judge.css'
import './memory/memory.css'
import './note/note.css'
import './question/question.css'
import './results/results.css'
import './schedule/schedule.css'
import './share/share.css'
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
import { ConversationActions } from './share/ConversationActions'
import { memoryVersion, recentChanges } from './memory/memory'
import { useMemory } from './memory/useMemory'
import { NotePanel } from './note/NotePanel'
import { lastUpdate, noteVersion, recentSections } from './note/note'
import { useNote } from './note/useNote'
import { SchedulesPanel } from './schedule/SchedulesPanel'
import { InlineCode } from './shared/InlineCode'
import { useSchedules } from './schedule/useSchedules'
import { useSession } from './session/useSession'
import { StatusBar } from './statusbar/StatusBar'
import { Timeline } from './timeline/Timeline'

type SideTab = 'timeline' | 'memory' | 'note' | 'schedules'

const TABS: { id: SideTab; label: string }[] = [
  { id: 'timeline', label: 'Timeline' },
  { id: 'memory', label: 'Mémoire' },
  { id: 'note', label: 'Note' },
  { id: 'schedules', label: 'Tâches planifiées' },
]

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
  const note = useNote(session.connected, noteVersion(state.turns))
  const schedules = useSchedules(session.connected, state.schedulesVersion)
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
        onSetConfig={session.setConfig}
        onSetPersona={session.setPersona}
      />

      <main className="conversation" aria-label="Conversation">
        <ConversationActions
          sessionId={state.session?.id ?? null}
          hasTurns={state.turns.length > (session.busy ? 1 : 0)} // a finished turn to show
          connected={session.connected}
          sharesVersion={state.sharesVersion}
          onShare={session.share}
          onUnshare={session.unshare}
        />
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
              <p>
                <InlineCode text={notice.message} />
              </p>
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
            onShowNote={() => setTab('note')}
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

      <aside
        className="sidepanel"
        aria-label="Étapes de l'agent, mémoire, note du comité et tâches planifiées"
      >
        <div className="tabs" role="tablist">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-controls="panel-side"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="tab-panel" role="tabpanel" id="panel-side" aria-labelledby={`tab-${tab}`}>
          {tab === 'timeline' && (
            <Timeline turn={state.turns[timelineIndex] ?? null} number={timelineIndex + 1} />
          )}
          {tab === 'memory' && (
            <MemoryPanel
              memory={memory}
              recent={recentChanges(state.turns)}
              canAct={canAct}
              onAction={session.uiAction}
            />
          )}
          {tab === 'note' && (
            <NotePanel
              note={note.note}
              revisions={note.revisions}
              recent={recentSections(state.turns)}
              last={lastUpdate(state.turns)}
              canAct={canAct}
              onAction={session.uiAction}
            />
          )}
          {tab === 'schedules' && (
            <SchedulesPanel
              schedules={schedules}
              personas={state.session?.personas ?? []}
              connected={session.connected} // a run does not wait for the conversation
              onRun={session.runSchedule}
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
