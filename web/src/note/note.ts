// The committee note (M4) as GET /api/documents/current gives it (docs/contracts/memory.md §7.5).

import type { DocumentOp, DocumentUpdated, NoteDocument, NoteRevision } from '../protocol'
import type { Turn } from '../session/reducer'

// Note calls answered so far: the panel reads the note again when it moves, after a write
// and also after a refused one (REVISION_CONFLICT: the note changed in the meantime)
export function noteVersion(turns: Turn[]): number {
  return turns.reduce(
    (total, turn) =>
      total + turn.steps.filter((step) => step.tool.startsWith('document_') && step.result !== null).length,
    0,
  )
}

// The last write of the note in the conversation: its sections are highlighted
export function lastUpdate(turns: Turn[]): DocumentUpdated | null {
  const last = [...turns].reverse().find((turn) => turn.documentUpdates.length > 0)
  return last?.documentUpdates.at(-1) ?? null
}

// Sections written by the last turn that wrote the note, with what was done to them
export function recentSections(turns: Turn[]): Map<string, DocumentOp> {
  const last = [...turns].reverse().find((turn) => turn.documentUpdates.length > 0)
  const changes = new Map<string, DocumentOp>()
  for (const update of last?.documentUpdates ?? []) {
    if (update.section_id !== null && update.op !== 'delete') changes.set(update.section_id, update.op)
  }
  return changes
}

export const CHANGE_LABEL: Record<DocumentOp, string> = {
  replace: 'modifiée',
  append: 'complétée',
  insert_after: 'nouvelle',
  delete: 'supprimée',
  restore: 'restaurée',
}

const REVISION_LABEL: Record<DocumentOp, string> = {
  replace: 'Section modifiée',
  append: 'Section complétée',
  insert_after: 'Section ajoutée',
  delete: 'Section supprimée',
  restore: 'Retour arrière',
}

// "Section modifiée « Rentabilité » · par l'agent"
export function revisionLabel(revision: NoteRevision, note: NoteDocument | null): string {
  const actor = revision.actor === 'user' ? 'par vous' : "par l'agent"
  if (revision.section_id === null) return `${REVISION_LABEL[revision.op]} · ${actor}`
  const title = note?.sections.find((section) => section.section_id === revision.section_id)?.title
  return `${REVISION_LABEL[revision.op]} « ${title ?? revision.section_id} » · ${actor}`
}

// "2026-10-28T14:02:00Z" → "28/10 14:02", in the browser's time zone
export function formatRevisionAt(at: string): string {
  const date = new Date(at)
  if (Number.isNaN(date.getTime())) return at
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function isBlank(note: NoteDocument): boolean {
  return note.sections.every((section) => section.content.trim() === '')
}

export const EXPORT_URL = '/api/documents/current/export?format=md'
