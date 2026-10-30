import { Fragment, useLayoutEffect, useRef } from 'react'
import type { Persona } from '../protocol'
import type { Reply } from '../question/QuestionCard'
import type { PersonaMark, Turn } from '../session/reducer'
import { AssistantMessage } from './AssistantMessage'

// Below this distance from the bottom, the list follows the new tokens
const STICK_THRESHOLD_PX = 48

interface Props {
  turns: Turn[]
  personaMarks: PersonaMark[]
  connected: boolean
  refusalKey: number
  onAnswer: (questionId: string, reply: Reply) => void
  canAsk: boolean
  onAsk: (question: string) => void
  // Absent on a shared page: no timeline, no memory panel
  onShowSteps?: (turnId: string) => void
  onShowMemory?: () => void
  onShowNote?: () => void
}

export function MessageList({
  turns,
  personaMarks,
  connected,
  refusalKey,
  onAnswer,
  canAsk,
  onAsk,
  onShowSteps,
  onShowMemory,
  onShowNote,
}: Props) {
  const listRef = useRef<HTMLDivElement>(null)
  const followBottom = useRef(true)

  // Auto-scroll, unless the user scrolled up to read an earlier answer
  useLayoutEffect(() => {
    const list = listRef.current
    if (list !== null && followBottom.current) list.scrollTop = list.scrollHeight
  }, [turns, personaMarks])

  const onScroll = () => {
    const list = listRef.current
    if (list === null) return
    followBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < STICK_THRESHOLD_PX
  }

  return (
    <div className="messages" ref={listRef} onScroll={onScroll}>
      <div className="thread">
        {turns.map((turn, index) => (
          <Fragment key={turn.id}>
            {personaMarks
              .filter((mark) => mark.afterTurns === index)
              .map((mark, number) => (
                <PersonaSeparator key={number} persona={mark.persona} />
              ))}
            <section className="turn" aria-label="Échange">
              {turn.origin === 'user' ? (
                <p className="bubble-user">{turn.userMessage}</p>
              ) : (
                // A turn started from the interface (memory or Note panel) has no user bubble
                <p className="ui-action">
                  Action depuis le panneau{' '}
                  {turn.steps[0]?.tool.startsWith('document_') ? 'Note' : 'Mémoire'} :{' '}
                  <code>{turn.steps[0]?.tool ?? 'outil'}</code>
                  {turn.steps[0]?.result && ` · ${turn.steps[0].result.summary}`}
                </p>
              )}
              {turn.origin === 'user' && (
                <AssistantMessage
                  turn={turn}
                  connected={connected}
                  refusalKey={refusalKey}
                  onAnswer={onAnswer}
                  canAsk={canAsk}
                  onAsk={onAsk}
                  onShowSteps={onShowSteps}
                  onShowMemory={onShowMemory}
                  onShowNote={onShowNote}
                />
              )}
            </section>
          </Fragment>
        ))}
        {personaMarks
          .filter((mark) => mark.afterTurns >= turns.length)
          .map((mark, number) => (
            <PersonaSeparator key={`end-${number}`} persona={mark.persona} />
          ))}
      </div>
    </div>
  )
}

// The profile changed here: the turns below are answered with it (A5, act 4)
function PersonaSeparator({ persona }: { persona: Persona }) {
  return (
    <p className="persona-mark" role="note">
      <span>
        Profil : <strong>{persona.name}</strong> · {persona.role}
      </span>
    </p>
  )
}
