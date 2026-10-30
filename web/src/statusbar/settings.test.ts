import { describe, expect, it } from 'vitest'
import type { ConfigView, SessionConfig } from '../protocol'
import { contextChoices, formatTemperature, readOnlyRows, resetPatch } from './settings'

const defaults: SessionConfig = { temperature: 0.1, num_ctx: 8192, judge_enabled: true, judge_model: null }

const view: ConfigView = {
  defaults,
  bounds: { temperature: [0, 1], num_ctx: [2048, 32768] },
  read_only: {
    ollama_host: 'http://localhost:11434',
    keep_alive: '30m',
    max_tool_iterations: 6,
    max_sql_retries: 2,
    row_limit: 200,
    query_timeout_ms: 5000,
    confirmation_timeout_s: 120,
    numeric_tolerance: 0.01,
  },
}

describe('settings (X1, set_config)', () => {
  it('offers the powers of two within the bounds of num_ctx', () => {
    expect(contextChoices([2048, 32768], 8192)).toEqual([2048, 4096, 8192, 16384, 32768])
  })

  it('keeps a context size of config.toml that is not a power of two', () => {
    expect(contextChoices([2048, 32768], 6000)).toEqual([2048, 4096, 6000, 8192, 16384, 32768])
  })

  it('resets only the settings changed in the session', () => {
    expect(resetPatch(defaults, defaults)).toEqual({})
    const changed = { ...defaults, num_ctx: 16384, judge_enabled: false }
    expect(resetPatch(defaults, changed)).toEqual({ num_ctx: 8192, judge_enabled: true })
    expect(resetPatch(defaults, { ...defaults, judge_model: 'gemma2:9b' })).toEqual({ judge_model: null })
  })

  it('writes the temperature and the fixed settings in French', () => {
    expect(formatTemperature(0.1)).toBe('0,10')
    const rows = Object.fromEntries(readOnlyRows(view))
    expect(rows["Délai d'une requête"]).toBe('5 s')
    expect(rows['Tolérance du juge sur les chiffres']).toBe('1 %')
    expect(rows['Délai de confirmation']).toBe('120 s')
  })
})
