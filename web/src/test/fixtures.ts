// Test helpers only (excluded from the app build): the same event files as the MockEngine
// and the contract tests (tests/fixtures/events).

import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { EngineEvent } from '../protocol'
import { chatReducer, initialState, type Turn } from '../session/reducer'

const FIXTURES = fileURLToPath(new URL('../../../tests/fixtures/events/', import.meta.url))

export const FIXTURE_FILES = readdirSync(FIXTURES).filter((name) => name.endsWith('.jsonl'))

export function fixture(name: string): EngineEvent[] {
  return readFileSync(`${FIXTURES}${name}`, 'utf-8')
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => JSON.parse(line) as EngineEvent)
}

// Turns rebuilt by the reducer from a list of events
export function turnsOf(events: EngineEvent[]): Turn[] {
  return chatReducer(initialState, { type: 'server', messages: events }).turns
}

// Events up to (and including) the first one matching the predicate
export function until(events: EngineEvent[], predicate: (event: EngineEvent) => boolean): EngineEvent[] {
  return events.slice(0, events.findIndex(predicate) + 1)
}
