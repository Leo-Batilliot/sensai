import { describe, expect, it } from 'vitest'
import { fixture, turnsOf, until } from '../test/fixtures'
import {
  EMPTY_MEMORY,
  entityName,
  entryKey,
  isEmpty,
  memoryVersion,
  parseAliases,
  recentChanges,
} from './memory'

const memoryTurns = () => turnsOf(fixture('memory.jsonl'))

describe('after a memory write', () => {
  it('counts the writes, so that the panel reads the memory again', () => {
    const events = fixture('memory.jsonl')
    expect(memoryVersion(turnsOf(until(events, (e) => e.type === 'turn_started')))).toBe(0)
    expect(memoryVersion(memoryTurns())).toBe(1) // glossary_define in act 2
  })

  it('marks the entry the agent has just learned as new', () => {
    expect(recentChanges(memoryTurns())).toEqual(new Map([[entryKey('glossary', 'client rentable'), 'create']]))
  })

  it('keeps the context given to the model for the chips under the answer', () => {
    const [, question] = memoryTurns()
    expect(question.context).toEqual([{ kind: 'glossary', ref: 'client rentable', label: 'client rentable' }])
  })
})

describe('display helpers', () => {
  const memory = {
    ...EMPTY_MEMORY,
    entities: [
      {
        ref: '12',
        type: 'client',
        name: 'Brasserie Lemaire',
        aliases: [],
        external_ref: null,
        attributes: {},
        updated_at: '',
      },
    ],
  }

  it('names the entity of a fact or a relation', () => {
    expect(entityName(memory, '12')).toBe('Brasserie Lemaire')
    expect(entityName(memory, '99')).toBe('entité 99')
    expect(entityName(memory, null)).toBeNull()
  })

  it('tells an empty memory apart', () => {
    expect(isEmpty(EMPTY_MEMORY)).toBe(true)
    expect(isEmpty(memory)).toBe(false)
  })

  it('reads the aliases typed in the edit field', () => {
    expect(parseAliases(' Lemaire, BL ,, ')).toEqual(['Lemaire', 'BL'])
    expect(parseAliases('')).toEqual([])
  })
})
