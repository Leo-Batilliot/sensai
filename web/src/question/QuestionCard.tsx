import { useEffect, useRef, useState, type FormEvent } from 'react'
import { formatCountdown } from '../hitl/confirmation'
import type { QuestionAnswered, QuestionAsked } from '../protocol'
import { ANSWER_MAX, closedQuestion } from './question'

const TICK_MS = 250
const URGENT_S = 30

export type Reply = { optionId: string } | { text: string }

interface Props {
  question: QuestionAsked
  answer: QuestionAnswered | null
  connected: boolean
  refusalKey: number // changes when the server refuses something: the card can be answered again
  onAnswer: (questionId: string, reply: Reply) => void
}

// The agent asks before answering (docs/keynote/scenario.md, act 1)
export function QuestionCard({ question, answer, connected, refusalKey, onAnswer }: Props) {
  if (answer !== null) {
    const closed = closedQuestion(question, answer)
    return (
      <p className="question-closed">
        <span className="muted">Question : </span>
        {question.question}{' '}
        {closed.answered ? (
          <>
            <span className="muted">→</span> <strong>{closed.text}</strong>
          </>
        ) : (
          <span className="muted">· {closed.text}</span>
        )}
      </p>
    )
  }
  return (
    <PendingQuestion
      key={question.question_id}
      question={question}
      connected={connected}
      refusalKey={refusalKey}
      onAnswer={onAnswer}
    />
  )
}

type PendingProps = Omit<Props, 'answer'>

// Mounted once per question: its mount time is the reception time of question_asked
function PendingQuestion({ question, connected, refusalKey, onAnswer }: PendingProps) {
  const [receivedAt] = useState(() => Date.now())
  const [now, setNow] = useState(receivedAt)
  const [text, setText] = useState('')
  const [sentAt, setSentAt] = useState<number | null>(null) // refusalKey when the answer left
  const firstRef = useRef<HTMLButtonElement & HTMLInputElement>(null)
  const titleId = `question-${question.question_id}`

  useEffect(() => {
    firstRef.current?.focus() // the chat input is disabled meanwhile: the card is the next step
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    return () => window.clearInterval(timer)
  }, [])

  // From reception, not from `ts` (§6.4). At zero the engine sends question_answered (timeout)
  const remaining = question.expires_in_s - (now - receivedAt) / 1000
  // A refused answer (INVALID_MESSAGE) leaves the question open: answering works again
  const sent = sentAt !== null && sentAt === refusalKey
  const disabled = sent || remaining <= 0 || !connected

  const reply = (value: Reply) => {
    if (disabled) return
    setSentAt(refusalKey)
    onAnswer(question.question_id, value)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const answer = text.trim()
    if (answer !== '') reply({ text: answer })
  }

  return (
    <section className="question-card" aria-labelledby={titleId}>
      <header className="question-header">
        <span className="question-mark" aria-hidden="true">
          ?
        </span>
        <h3 id={titleId}>L'agent a besoin d'une précision</h3>
        <span
          className={`question-timer${remaining <= URGENT_S ? ' urgent' : ''}`}
          role="timer"
          aria-label={`Temps restant : ${formatCountdown(remaining)}`}
        >
          ⏱ {formatCountdown(remaining)}
        </span>
      </header>
      <p className="question-text">{question.question}</p>
      {question.reason !== '' && <p className="question-reason">{question.reason}</p>}

      {question.options.length > 0 && (
        <div className="question-options">
          {question.options.map((option, index) => (
            <button
              key={option.id}
              ref={index === 0 ? firstRef : undefined}
              type="button"
              disabled={disabled}
              onClick={() => reply({ optionId: option.id })}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      {question.allow_free_text && (
        <form className="question-free" onSubmit={submit}>
          <label className="sr-only" htmlFor={`${titleId}-text`}>
            Votre réponse
          </label>
          <input
            id={`${titleId}-text`}
            ref={question.options.length === 0 ? firstRef : undefined}
            value={text}
            maxLength={ANSWER_MAX}
            placeholder={question.options.length > 0 ? 'Autre réponse…' : 'Votre réponse…'}
            disabled={disabled}
            onChange={(event) => setText(event.target.value)}
          />
          <button type="submit" disabled={disabled || text.trim() === ''}>
            Répondre
          </button>
        </form>
      )}

      <p className="question-footer muted" role="status">
        {!connected
          ? 'Connexion perdue : la question sera annulée.'
          : sent
            ? 'Réponse envoyée…'
            : remaining <= 0
              ? "Délai dépassé, l'agent continue avec une hypothèse…"
              : "Sans réponse avant la fin du délai, l'agent fera une hypothèse et la dira."}
      </p>
    </section>
  )
}
