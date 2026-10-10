import { useLayoutEffect, useRef } from 'react'
import type { Turn } from '../session/reducer'
import { AssistantMessage } from './AssistantMessage'

// Below this distance from the bottom, the list follows the new tokens
const STICK_THRESHOLD_PX = 48

export function MessageList({ turns }: { turns: Turn[] }) {
  const listRef = useRef<HTMLDivElement>(null)
  const followBottom = useRef(true)

  // Auto-scroll, unless the user scrolled up to read an earlier answer
  useLayoutEffect(() => {
    const list = listRef.current
    if (list !== null && followBottom.current) list.scrollTop = list.scrollHeight
  }, [turns])

  const onScroll = () => {
    const list = listRef.current
    if (list === null) return
    followBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < STICK_THRESHOLD_PX
  }

  return (
    <div className="messages" ref={listRef} onScroll={onScroll}>
      <div className="thread">
        {turns.map((turn) => (
          <section key={turn.id} className="turn" aria-label="Échange">
            {turn.origin === 'user' ? (
              <p className="bubble-user">{turn.userMessage}</p>
            ) : (
              // A turn started from the interface (memory panel) has no user bubble
              <p className="ui-action">
                Action depuis l'interface : <code>{turn.steps[0]?.tool ?? 'outil'}</code>
              </p>
            )}
            {turn.origin === 'user' && <AssistantMessage turn={turn} />}
          </section>
        ))}
      </div>
    </div>
  )
}
