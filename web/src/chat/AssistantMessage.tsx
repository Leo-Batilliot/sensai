import { useMemo } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { confirmationOutcome } from '../hitl/confirmation'
import { MARK_PREFIX, highlight } from '../judge/judge'
import { JudgeBadge } from '../judge/JudgeBadge'
import { KeyFigures } from '../judge/KeyFigures'
import { QuestionCard, type Reply } from '../question/QuestionCard'
import { ResultTable } from '../results/ResultTable'
import { asRowSet } from '../results/table'
import type { Turn } from '../session/reducer'
import { messageQueries, stepStatus, stepsSummary } from '../session/steps'
import { SqlBlock } from '../sql/SqlBlock'

// What the agent is doing, shown above the answer while it works
function activity(turn: Turn): string | null {
  if (turn.status === 'awaiting_confirmation') return 'En attente de votre confirmation…'
  // awaiting_answer: the question card says it
  if (turn.status !== 'running') return null
  if (turn.currentTool !== null) return `Appelle ${turn.currentTool}…`
  if (turn.content === '') return 'Réfléchit…'
  return null
}

// The final answer, with the passages checked by the judge highlighted (WEB-09)
function Answer({ turn, streaming }: { turn: Turn; streaming: boolean }) {
  const { content, judge } = turn
  const highlighted = useMemo(
    () => (judge === null || judge === 'pending' || streaming ? null : highlight(content, judge)),
    [content, judge, streaming],
  )
  const components = useMemo<Components | undefined>(() => {
    if (highlighted === null) return undefined
    return {
      a({ href, children }) {
        const mark = href?.startsWith(MARK_PREFIX)
          ? highlighted.marks[Number(href.slice(MARK_PREFIX.length))]
          : undefined
        if (mark === undefined) return <a href={href}>{children}</a>
        return (
          <mark className={`claim claim-${mark.kind}`} title={mark.note}>
            {children}
          </mark>
        )
      },
    }
  }, [highlighted])

  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {highlighted?.markdown ?? content}
      </Markdown>
      {streaming && <span className="cursor" aria-hidden="true" />}
    </div>
  )
}

interface Props {
  turn: Turn
  connected: boolean
  refusalKey: number
  onAnswer: (questionId: string, reply: Reply) => void
  canAsk: boolean
  onAsk: (question: string) => void
  onShowSteps?: (turnId: string) => void
  onShowMemory?: () => void
}

export function AssistantMessage({
  turn,
  connected,
  refusalKey,
  onAnswer,
  canAsk,
  onAsk,
  onShowSteps,
  onShowMemory,
}: Props) {
  const streaming = turn.status === 'running'
  const label = activity(turn)
  const outcome = confirmationOutcome(turn)
  const report = turn.judge !== null && turn.judge !== 'pending' ? turn.judge.report : null

  return (
    <article className="assistant" aria-busy={streaming}>
      <span className="avatar" aria-hidden="true">
        S
      </span>
      <div className="assistant-body">
        {(turn.judge !== null || turn.steps.length > 0) && (
          <div className="message-meta">
            {turn.judge !== null && <JudgeBadge judge={turn.judge} />}
            {turn.steps.length > 0 &&
              (onShowSteps !== undefined ? (
                <button type="button" className="steps-link" onClick={() => onShowSteps(turn.id)}>
                  <span aria-hidden="true">▶</span> {stepsSummary(turn)}
                </button>
              ) : (
                <span className="steps-link">{stepsSummary(turn)}</span>
              ))}
          </div>
        )}
        {turn.context.length > 0 && (
          // What was given to the model, not what it applied (memory.md §4)
          <p className="context-chips">
            <span className="muted">Contexte mémoire fourni :</span>
            {turn.context.map((entry) =>
              onShowMemory !== undefined ? (
                <button
                  key={`${entry.kind}:${entry.ref}`}
                  type="button"
                  className="chip chip-small"
                  title="Voir la mémoire de l'agent"
                  onClick={onShowMemory}
                >
                  {entry.label}
                </button>
              ) : (
                <span key={`${entry.kind}:${entry.ref}`} className="chip chip-small">
                  {entry.label}
                </span>
              ),
            )}
          </p>
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
        {/* After the reasoning that led to it, before the answer it changes */}
        {turn.question !== null && (
          <QuestionCard
            question={turn.question}
            answer={turn.answer}
            connected={connected}
            refusalKey={refusalKey}
            onAnswer={onAnswer}
          />
        )}
        {turn.content !== '' && <Answer turn={turn} streaming={streaming} />}
        {outcome !== null && (
          // Kept in the message: the toast disappears, this stays
          <p className={`hitl-outcome tone-${outcome.tone}`}>
            <strong>{outcome.title}</strong> · {outcome.detail}
            {outcome.comment !== null && <span className="muted"> · « {outcome.comment} »</span>}
          </p>
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
        {report !== null && <KeyFigures report={report} canAsk={canAsk} onAsk={onAsk} />}
      </div>
    </article>
  )
}
