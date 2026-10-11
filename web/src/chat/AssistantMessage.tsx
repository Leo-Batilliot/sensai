import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ResultTable } from '../results/ResultTable'
import { asRowSet } from '../results/table'
import type { Turn } from '../session/reducer'
import { messageQueries, stepStatus, stepsSummary } from '../session/steps'
import { SqlBlock } from '../sql/SqlBlock'

// What the agent is doing, shown above the answer while it works
function activity(turn: Turn): string | null {
  if (turn.status === 'awaiting_confirmation') return 'En attente de votre confirmation…'
  if (turn.status !== 'running') return null
  if (turn.currentTool !== null) return `Appelle ${turn.currentTool}…`
  if (turn.content === '') return 'Réfléchit…'
  return null
}

interface Props {
  turn: Turn
  onShowSteps: (turnId: string) => void
}

export function AssistantMessage({ turn, onShowSteps }: Props) {
  const streaming = turn.status === 'running'
  const label = activity(turn)

  return (
    <article className="assistant" aria-busy={streaming}>
      <span className="avatar" aria-hidden="true">
        S
      </span>
      <div className="assistant-body">
        {turn.steps.length > 0 && (
          <button type="button" className="steps-link" onClick={() => onShowSteps(turn.id)}>
            <span aria-hidden="true">▶</span> {stepsSummary(turn)}
          </button>
        )}
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
        {messageQueries(turn).map((step) => {
          const rowSet = step.result === null ? null : asRowSet(step.result.data)
          const resultId = rowSet === null ? null : `result-${turn.id}-${step.callId}`
          return (
            <div key={step.callId} className="query">
              <SqlBlock step={step} status={stepStatus(turn, step)} resultId={resultId} />
              {rowSet !== null && resultId !== null && <ResultTable id={resultId} rowSet={rowSet} />}
            </div>
          )
        })}
      </div>
    </article>
  )
}
