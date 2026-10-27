import type { Badge } from '../protocol'
import type { ToolStep, Turn } from '../session/reducer'
import {
  STEP_STATUS_LABEL,
  formatDuration,
  shortToolName,
  stepStatus,
  stepsSummary,
  type StepStatus,
} from '../session/steps'
import { CopyButton } from '../shared/CopyButton'

const STATUS_ICON: Record<StepStatus, string> = {
  running: '◌',
  confirmation: '!',
  question: '?',
  ok: '✓',
  error: '✗',
  rejected: '⊘',
  stopped: '–',
}

const BADGE: Record<Badge, { label: string; status: StepStatus }> = {
  verified: { label: 'Vérifiée', status: 'ok' },
  needs_review: { label: 'À vérifier', status: 'confirmation' },
  contradicted: { label: 'Contredite', status: 'error' },
  unavailable: { label: 'Vérification indisponible', status: 'stopped' },
}

interface Props {
  turn: Turn | null
  number: number // "Tour 3"
}

// Steps of the agent during one turn (docs/keynote/ui/Timeline): one line per tool call
export function Timeline({ turn, number }: Props) {
  if (turn === null) {
    return (
      <p className="muted">Les étapes de l'agent s'afficheront ici pendant qu'il travaille.</p>
    )
  }

  return (
    <div className="timeline">
      <p className="timeline-header">
        <strong>Tour {number}</strong> <span className="muted">· {stepsSummary(turn)}</span>
      </p>
      {turn.steps.length === 0 && turn.judge === null ? (
        <p className="muted">
          {turn.status === 'running'
            ? 'Aucun outil appelé pour le moment.'
            : 'Aucun outil appelé pendant ce tour.'}
        </p>
      ) : (
        <ol className="steps">
          {turn.steps.map((step) => (
            <StepItem key={step.callId} step={step} status={stepStatus(turn, step)} />
          ))}
          {turn.judge !== null && <JudgeItem judge={turn.judge} />}
        </ol>
      )}
    </div>
  )
}

function StatusIcon({ status }: { status: StepStatus }) {
  return (
    <span className={`step-icon step-${status}`}>
      <span aria-hidden="true">{STATUS_ICON[status]}</span>
      <span className="sr-only">{STEP_STATUS_LABEL[status]}</span>
    </span>
  )
}

function StepItem({ step, status }: { step: ToolStep; status: StepStatus }) {
  const args = JSON.stringify(step.arguments, null, 2)
  const failed = status === 'error' || status === 'rejected'
  const detail = failed ? (step.result?.error?.message ?? step.result?.summary) : step.result?.summary

  return (
    <li className="step">
      <details>
        <summary>
          <StatusIcon status={status} />
          <span className="step-name">
            {step.server ?? 'sensai'} · <strong>{shortToolName(step)}</strong>
          </span>
          <span className="step-duration muted">
            {step.result !== null ? formatDuration(step.result.duration_ms) : STEP_STATUS_LABEL[status]}
          </span>
        </summary>
        <div className="step-arguments">
          {Object.keys(step.arguments).length === 0 ? (
            <p className="muted">Aucun argument.</p>
          ) : (
            <>
              <pre>{args}</pre>
              <CopyButton text={args} />
            </>
          )}
        </div>
      </details>
      {detail !== undefined && (
        <p className={`step-detail${failed ? ' step-detail-error' : ''}`}>{detail}</p>
      )}
    </li>
  )
}

function JudgeItem({ judge }: { judge: NonNullable<Turn['judge']> }) {
  const badge = judge === 'pending' ? null : BADGE[judge.badge]
  return (
    <li className="step step-judge">
      <div className="step-line">
        <StatusIcon status={badge?.status ?? 'running'} />
        <span className="step-name">
          juge · <strong>vérification</strong>
        </span>
        <span className="step-duration muted">
          {judge === 'pending' ? 'en cours' : formatDuration(judge.duration_ms)}
        </span>
      </div>
      <p className="step-detail">{badge === null ? 'Vérification de la réponse…' : badge.label}</p>
    </li>
  )
}
