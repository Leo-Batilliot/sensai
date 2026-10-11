import { describe, expect, it } from 'vitest'
import type { EngineEvent } from '../protocol'
import { asRowSet } from '../results/table'
import { fixture, turnsOf as turns, until } from '../test/fixtures'
import { formatDuration, messageQueries, shortToolName, stepStatus, stepsSummary } from './steps'

describe('nominal fixture (act 1)', () => {
  const [turn] = turns(fixture('nominal.jsonl'))

  it('has one timeline line per tool call, with the failed attempt visible', () => {
    expect(turn.steps.map((step) => `${step.server} · ${shortToolName(step)}`)).toEqual([
      'time · get_current_time',
      'sql_toolbox · list_tables',
      'sql_toolbox · describe_table',
      'sql_toolbox · run_select',
      'sql_toolbox · run_select',
    ])
    expect(turn.steps.map((step) => stepStatus(turn, step))).toEqual(['ok', 'ok', 'ok', 'error', 'ok'])
    expect(turn.steps[3].result?.error?.message).toBe('no such column: m.margin')
  })

  it('shows only the corrected query under the answer, with its table', () => {
    const queries = messageQueries(turn)
    expect(queries.map((step) => step.callId)).toEqual(['k-5'])
    const rowSet = asRowSet(queries[0].result?.data ?? null)
    expect(rowSet?.columns).toEqual(['name', 'marge_eur', 'delai_moyen_jours'])
    expect(rowSet?.rows).toHaveLength(5)
    expect(rowSet?.truncated).toBe(false)
  })

  it('links the message to its steps', () => {
    expect(stepsSummary(turn)).toBe('5 étapes · 4,1 s')
  })

  it('shows a step as running until its result arrives', () => {
    const events = fixture('nominal.jsonl')
    const [running] = turns(until(events, (event) => event.type === 'tool_call'))
    expect(stepStatus(running, running.steps[0])).toBe('running')
  })
})

describe('HITL fixtures', () => {
  it('waits for the confirmation, then shows the refused write', () => {
    const events = fixture('hitl_reject.jsonl')
    const [waiting] = turns(until(events, (event) => event.type === 'confirmation_required'))
    expect(stepStatus(waiting, waiting.steps[0])).toBe('confirmation')
    expect(messageQueries(waiting).map((step) => step.sql.operation)).toEqual(['DELETE'])

    const [done] = turns(events)
    expect(stepStatus(done, done.steps[0])).toBe('rejected')
    expect(messageQueries(done)).toEqual([]) // nothing ran
  })

  it('keeps the approved write under the answer, without a table', () => {
    const [done] = turns(fixture('hitl_approve.jsonl'))
    const [query] = messageQueries(done)
    expect(stepStatus(done, query)).toBe('ok')
    expect(asRowSet(query.result?.data ?? null)).toBeNull()
  })
})

describe('errors fixture', () => {
  it('marks every attempt as failed when the agent gives up', () => {
    const all = turns(fixture('errors.jsonl'))
    const last = all[all.length - 1]
    expect(last.status).toBe('failed')
    expect(last.steps.map((step) => stepStatus(last, step))).toEqual(Array(6).fill('error'))
    expect(messageQueries(last)).toEqual([])
  })

  it('shows a step without result as interrupted when the turn has ended', () => {
    const events = fixture('nominal.jsonl')
    const partial = until(events, (event) => event.type === 'tool_call')
    const [turn] = turns([
      ...partial,
      { ...partial[0], type: 'error', code: 'INTERNAL', message: 'boom', recoverable: true } as EngineEvent,
    ])
    expect(stepStatus(turn, turn.steps[0])).toBe('stopped')
  })
})

describe('formatDuration', () => {
  it('uses ms below one second, then seconds in French', () => {
    expect(formatDuration(120)).toBe('120 ms')
    expect(formatDuration(1900)).toBe('1,9 s')
    expect(formatDuration(2000)).toBe('2 s')
  })
})
