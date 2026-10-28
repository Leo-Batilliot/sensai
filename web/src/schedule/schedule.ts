// Scheduled tasks as the panel words them (X5, websocket.md §4.11, §5.2 and §9).
// The timetable, the runs and their badge come from the server: the front only words them.

import type { Badge, ScheduleInfo, ScheduleRun, ScheduleTrigger } from '../protocol'

const DAYS: Record<string, string> = {
  MON: 'lundi',
  TUE: 'mardi',
  WED: 'mercredi',
  THU: 'jeudi',
  FRI: 'vendredi',
  SAT: 'samedi',
  SUN: 'dimanche',
}

// "point-lundi" -> "Point lundi"
export function scheduleTitle(id: string): string {
  const words = id.replace(/[-_]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

// "07:00" -> "7 h", "18:30" -> "18 h 30"
function hour(clock: string): string {
  const [hours, minutes] = clock.split(':')
  return `${Number(hours)} h${minutes === '00' ? '' : ` ${minutes}`}`
}

// "Chaque lundi à 7 h", "Chaque jour à 18 h 30", "Toutes les 60 minutes"
export function timetableLabel(task: Pick<ScheduleInfo, 'at' | 'every_minutes'>): string {
  if (task.at === null) {
    return task.every_minutes === 1 ? 'Chaque minute' : `Toutes les ${task.every_minutes} minutes`
  }
  const [day, clock] = task.at.split(' ')
  return day === 'DAILY' ? `Chaque jour à ${hour(clock)}` : `Chaque ${DAYS[day] ?? day} à ${hour(clock)}`
}

const when = new Intl.DateTimeFormat('fr-FR', {
  weekday: 'short',
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export function formatWhen(epoch: number): string {
  return when.format(new Date(epoch * 1000))
}

export function triggerLabel(trigger: ScheduleTrigger): string {
  return trigger === 'manual' ? 'à la demande' : "à l'heure prévue"
}

export type RunTone = 'pending' | 'ok' | 'warn' | 'bad' | 'unavailable'

export interface RunView {
  tone: RunTone // same tones as the judge's badge (judge.ts)
  icon: string // always shown: the colour alone is not enough
  label: string
}

const BADGES: Record<Badge, RunView> = {
  verified: { tone: 'ok', icon: '✓', label: 'Vérifiée' },
  needs_review: { tone: 'warn', icon: '!', label: 'À vérifier' },
  contradicted: { tone: 'bad', icon: '✗', label: 'Contredite' },
  unavailable: { tone: 'unavailable', icon: '⊘', label: 'Vérification indisponible' },
}

export function runView(run: ScheduleRun): RunView {
  if (run.status === null) return { tone: 'pending', icon: '◌', label: 'En cours…' }
  if (run.status === 'error') return { tone: 'bad', icon: '✗', label: 'Échec' }
  if (run.badge === null) return { tone: 'unavailable', icon: '–', label: 'Sans vérification' }
  return BADGES[run.badge] ?? { tone: 'unavailable', icon: '?', label: 'Vérification' }
}

export function isRunning(task: ScheduleInfo): boolean {
  return task.last_runs[0]?.status === null
}

// Shown in a new tab, never a path given by the browser (websocket.md §9)
export function reportUrl(runId: string): string {
  return `/api/schedules/runs/${encodeURIComponent(runId)}/report`
}
