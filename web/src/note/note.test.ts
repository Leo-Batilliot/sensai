import { describe, expect, it } from 'vitest'
import type { NoteDocument, NoteRevision } from '../protocol'
import { fixture, turnsOf, until } from '../test/fixtures'
import { formatRevisionAt, isBlank, lastUpdate, noteVersion, recentSections, revisionLabel } from './note'

const noteTurns = () => turnsOf(fixture('document.jsonl'))

const note: NoteDocument = {
  id: 'note-comite',
  title: 'Note au comité de direction',
  revision: 2,
  sections: [
    { section_id: 'synthese', position: 1, title: 'Synthèse', content: '', updated_at: '2026-10-28T09:00:00Z' },
    {
      section_id: 'rentabilite',
      position: 2,
      title: 'Rentabilité',
      content: '3 clients rentables',
      updated_at: '2026-10-28T09:05:00Z',
    },
  ],
}

describe('after the agent writes the note (act 2)', () => {
  it('keeps the write on its turn, for the chip under the answer', () => {
    const [turn] = noteTurns()
    expect(turn.documentUpdates.map((update) => update.summary)).toEqual([
      'Section « Rentabilité » mise à jour (révision 1)',
    ])
  })

  it('counts the note calls, so that the panel reads the note again', () => {
    const events = fixture('document.jsonl')
    expect(noteVersion(turnsOf(until(events, (e) => e.type === 'turn_started')))).toBe(0)
    expect(noteVersion(noteTurns())).toBe(2) // document_read, then document_patch
  })

  it('highlights only the section that changed', () => {
    expect(recentSections(noteTurns())).toEqual(new Map([['rentabilite', 'replace']]))
    expect(lastUpdate(noteTurns())?.revision).toBe(1)
  })

  it('highlights nothing before any write', () => {
    expect(recentSections([])).toEqual(new Map())
    expect(lastUpdate([])).toBeNull()
  })
})

describe('display helpers', () => {
  const revision = (fields: Partial<NoteRevision>): NoteRevision => ({
    revision: 1,
    op: 'replace',
    section_id: 'rentabilite',
    actor: 'agent',
    at: '2026-10-28T09:05:00Z',
    ...fields,
  })

  it('says what each revision did, and who did it', () => {
    expect(revisionLabel(revision({}), note)).toBe("Section modifiée « Rentabilité » · par l'agent")
    expect(revisionLabel(revision({ op: 'delete', section_id: 'risques', actor: 'user' }), note)).toBe(
      'Section supprimée « risques » · par vous', // a deleted section keeps its id
    )
    expect(revisionLabel(revision({ op: 'restore', section_id: null, actor: 'user' }), note)).toBe(
      'Retour arrière · par vous',
    )
  })

  it('shows the time of a revision, and leaves an unreadable one as is', () => {
    expect(formatRevisionAt('2026-10-28T09:05:00Z')).toMatch(/^\d{2}\/10 \d{2}:05$/)
    expect(formatRevisionAt('hier')).toBe('hier')
  })

  it('knows when the note is still empty', () => {
    expect(isBlank(note)).toBe(false)
    expect(isBlank({ ...note, sections: [{ ...note.sections[0] }] })).toBe(true)
  })
})
