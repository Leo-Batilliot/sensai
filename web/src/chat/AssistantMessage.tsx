import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Turn } from '../session/reducer'

// What the agent is doing, shown above the answer while it works
function activity(turn: Turn): string | null {
  if (turn.status === 'awaiting_confirmation') return 'En attente de votre confirmation…'
  if (turn.status !== 'running') return null
  if (turn.currentTool !== null) return `Appelle ${turn.currentTool}…`
  if (turn.content === '') return 'Réfléchit…'
  return null
}

export function AssistantMessage({ turn }: { turn: Turn }) {
  const streaming = turn.status === 'running'
  const label = activity(turn)

  return (
    <article className="assistant" aria-busy={streaming}>
      <span className="avatar" aria-hidden="true">
        S
      </span>
      <div className="assistant-body">
        {label !== null && (
          <p className="activity" role="status">
            {label}
          </p>
        )}
        {turn.thinking !== '' && (
          <details className="thinking">
            <summary>Raisonnement</summary>
            <p>{turn.thinking}</p>
          </details>
        )}
        {turn.content !== '' && (
          <div className="markdown">
            <Markdown remarkPlugins={[remarkGfm]}>{turn.content}</Markdown>
            {streaming && <span className="cursor" aria-hidden="true" />}
          </div>
        )}
        {turn.status === 'interrupted' && <p className="interrupted">Réponse interrompue.</p>}
        {turn.error !== null && (
          <p className="turn-error" role="alert">
            {turn.error.message}
          </p>
        )}
      </div>
    </article>
  )
}
