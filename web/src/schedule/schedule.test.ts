import { describe, expect, it } from 'vitest'
import type { ScheduleInfo, ScheduleRun, ScheduleRunFinished, ScheduleRunStarted } from '../protocol'
import { chatReducer, initialState } from '../session/reducer'
import { isRunning, reportUrl, runView, scheduleTitle, timetableLabel, triggerLabel } from './schedule'

function run(fields: Partial<ScheduleRun>): ScheduleRun {
  return {
    run_id: 'r-1',
    trigger: 'manual',
    started_at: 1761548400,
    finished_at: 1761548460,
    status: 'ok',
    badge: 'verified',
    report_path: 'reports/planifie/point-lundi-2026-10-27.md',
    error: null,
    ...fields,
  }
}

const TASK: ScheduleInfo = {
  id: 'point-lundi',
  at: 'MON 07:00',
  every_minutes: null,
  question: 'Prépare le point du lundi',
  persona: 'analyste',
  output: 'reports/planifie/point-lundi-{date}.md',
  enabled: true,
  next_run: 1761548400,
  last_runs: [],
}

describe('scheduled tasks (X5, SCHED-01)', () => {
  it('words the timetable as the scenario says it', () => {
    expect(scheduleTitle('point-lundi')).toBe('Point lundi')
    expect(timetableLabel({ at: 'MON 07:00', every_minutes: null })).toBe('Chaque lundi à 7 h')
    expect(timetableLabel({ at: 'DAILY 18:30', every_minutes: null })).toBe('Chaque jour à 18 h 30')
    expect(timetableLabel({ at: null, every_minutes: 60 })).toBe('Toutes les 60 minutes')
    expect(triggerLabel('schedule')).toBe("à l'heure prévue")
  })

  it('shows a run as running, failed, or with the badge of the judge', () => {
    expect(runView(run({ status: null, badge: null, finished_at: null })).label).toBe('En cours…')
    const failure = { code: 'PATH_FORBIDDEN', message: 'Rapport refusé' }
    expect(runView(run({ status: 'error', badge: null, error: failure })).tone).toBe('bad')
    expect(runView(run({})).label).toBe('Vérifiée')
    expect(runView(run({ badge: 'contradicted' })).tone).toBe('bad')
    expect(runView(run({ badge: null })).label).toBe('Sans vérification')
  })

  it('knows when a task is still running: its last run has no status yet', () => {
    expect(isRunning(TASK)).toBe(false)
    expect(isRunning({ ...TASK, last_runs: [run({ status: null })] })).toBe(true)
    expect(isRunning({ ...TASK, last_runs: [run({}), run({ status: null })] })).toBe(false)
  })

  it('links the report by run id, never by path', () => {
    expect(reportUrl('3f2b/..')).toBe('/api/schedules/runs/3f2b%2F../report')
  })

  it('reads the list again on each run, outside the conversation', () => {
    const common = { session_id: 's', ts: 0, schedule_id: 'point-lundi', run_id: 'r-1' }
    const started: ScheduleRunStarted = { type: 'schedule_run_started', trigger: 'manual', ...common }
    const finished: ScheduleRunFinished = {
      type: 'schedule_run_finished',
      status: 'ok',
      badge: 'verified',
      report_path: 'reports/planifie/point-lundi-2026-10-27.md',
      error: null,
      ...common,
    }
    const state = chatReducer(initialState, { type: 'server', messages: [started, finished] })
    expect(state.schedulesVersion).toBe(2)
    expect(state.turns).toEqual([])
    expect(state.notices).toEqual([])
  })
})
