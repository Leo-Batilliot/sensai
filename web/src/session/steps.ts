// What the interface says about the tool calls of a turn (timeline and SQL blocks).

import type { SqlGenerated } from '../protocol'
import type { ToolStep, Turn } from './reducer'

export type StepStatus =
  | 'running'
  | 'confirmation'
  | 'question'
  | 'ok'
  | 'error'
  | 'rejected'
  | 'stopped'

export const STEP_STATUS_LABEL: Record<StepStatus, string> = {
  running: 'en cours',
  confirmation: 'en attente de confirmation',
  question: 'en attente de votre réponse',
  ok: 'OK',
  error: 'erreur',
  rejected: 'refusée',
  stopped: 'interrompue',
}

export function stepStatus(turn: Turn, step: ToolStep): StepStatus {
  if (step.result !== null) {
    if (step.result.ok) return 'ok'
    // Refused, expired or interrupted confirmation (events.md §3.7): not a failure of the tool
    return step.result.error?.code === 'ACTION_REJECTED' ? 'rejected' : 'error'
  }
  if (turn.status === 'awaiting_confirmation' && turn.confirmation?.call_id === step.callId) {
    return 'confirmation'
  }
  if (turn.status === 'awaiting_answer' && turn.question?.call_id === step.callId) return 'question'
  const working = ['running', 'awaiting_confirmation', 'awaiting_answer'].includes(turn.status)
  return working ? 'running' : 'stopped'
}

// "sql_toolbox__run_select" → "run_select": the server is already shown next to it
export function shortToolName(step: ToolStep): string {
  const prefix = step.server === null ? null : `${step.server}__`
  return prefix !== null && step.tool.startsWith(prefix) ? step.tool.slice(prefix.length) : step.tool
}

export type QueryStep = ToolStep & { sql: SqlGenerated }

// Queries shown under the answer: the ones that ran or are still running. Failed attempts
// (SQL error, refused write) stay in the timeline, with the corrected attempt after them.
export function messageQueries(turn: Turn): QueryStep[] {
  return turn.steps.filter(
    (step): step is QueryStep => step.sql !== null && step.result?.ok !== false,
  )
}

const seconds = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${seconds.format(ms / 1000)} s`
}

// "6 étapes · 1,9 s": link from a message to its turn in the timeline
export function stepsSummary(turn: Turn): string {
  const count = `${turn.steps.length} étape${turn.steps.length > 1 ? 's' : ''}`
  return turn.durationMs === null ? count : `${count} · ${formatDuration(turn.durationMs)}`
}
