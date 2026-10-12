// HITL confirmation (websocket.md §4.3, §6.3): what the modal shows and what the interface
// says once the engine has resolved the action. The front never decides anything itself.

import type { ConfirmationRequired, Impact } from '../protocol'
import type { Turn } from '../session/reducer'

// The confirmation the user must answer now, if any
export function pendingConfirmation(turns: Turn[]): ConfirmationRequired | null {
  const last = turns[turns.length - 1]
  return last?.status === 'awaiting_confirmation' ? last.confirmation : null
}

export type OutcomeTone = 'ok' | 'warn' | 'bad' | 'neutral'

export interface Outcome {
  key: string // changes with each new state of the action: one toast per change
  final: boolean // false while an approved action is still running
  tone: OutcomeTone
  title: string
  detail: string
  comment: string | null
}

// What happened to the last confirmation of a turn, null when there was none
export function confirmationOutcome(turn: Turn): Outcome | null {
  const { confirmation, resolution } = turn
  if (confirmation === null || resolution === null) return null
  if (resolution.action_id !== confirmation.action_id) return null
  const result = turn.steps.find((step) => step.callId === confirmation.call_id)?.result ?? null
  const outcome = (state: string, tone: OutcomeTone, title: string, detail: string): Outcome => ({
    key: `${confirmation.action_id}:${state}`,
    final: state !== 'running',
    tone,
    title,
    detail,
    comment: resolution.comment,
  })

  if (resolution.decision === 'approve') {
    if (result === null) return outcome('running', 'neutral', 'Action confirmée', 'Exécution en cours…')
    if (result.ok) return outcome('done', 'ok', 'Action confirmée', result.summary)
    return outcome(
      'failed',
      'bad',
      "Action confirmée, mais l'exécution a échoué",
      result.error?.message ?? result.summary,
    )
  }
  // decision "reject" or "timeout": the tool was not run (events.md §3.7)
  const unchanged = "rien n'a été modifié"
  switch (resolution.reason) {
    case 'timeout':
      return outcome('timeout', 'warn', 'Délai dépassé', `Action annulée automatiquement, ${unchanged}.`)
    case 'cancelled':
      return outcome('cancelled', 'neutral', 'Action annulée', `La réponse a été arrêtée, ${unchanged}.`)
    case 'disconnected':
      return outcome(
        'disconnected',
        'warn',
        'Action refusée automatiquement',
        `La connexion a été perdue, ${unchanged}.`,
      )
    case 'user':
      return outcome('rejected', 'neutral', 'Action refusée', "Rien n'a été modifié. L'agent en tient compte.")
  }
}

const count = new Intl.NumberFormat('fr-FR')

export function rowsLabel(rows: number): string {
  return `${count.format(rows)} ligne${rows > 1 ? 's' : ''}`
}

// "−27": signed difference of COUNT(*) for one table, with a real minus sign
export function formatDelta(delta: number): string {
  if (delta === 0) return '0'
  return `${delta < 0 ? '−' : '+'}${count.format(Math.abs(delta))}`
}

export interface ImpactLine {
  table: string
  delta: number
}

// per_table, biggest change first. Empty for an UPDATE (differences are all 0, P-16) and
// for a statement that SQLite would reject because of protected rows.
export function impactLines(impact: Impact): ImpactLine[] {
  return Object.entries(impact.per_table)
    .filter(([, delta]) => delta !== 0)
    .map(([table, delta]) => ({ table, delta }))
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
}

// The confirm button repeats what will happen (docs/keynote/ui/ModaleHITL, annotation 7)
export function confirmLabel(impact: Impact): string {
  const rows = rowsLabel(impact.rows_affected)
  if (impact.rows_affected === 0) return 'Confirmer'
  switch (impact.operation) {
    case 'DELETE':
      return `Confirmer la suppression de ${rows}`
    case 'UPDATE':
      return `Confirmer la modification de ${rows}`
    case 'INSERT':
      return `Confirmer l'ajout de ${rows}`
    default:
      return 'Confirmer'
  }
}

// 107 → "1:47"
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.ceil(seconds))
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`
}
