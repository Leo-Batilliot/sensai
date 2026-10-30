import { useState, type FormEvent } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DocumentOp, DocumentUpdated, NoteDocument, NoteRevision, NoteSection } from '../protocol'
import { CHANGE_LABEL, EXPORT_URL, formatRevisionAt, isBlank, revisionLabel } from './note'

export type NoteAction = (
  tool: 'document_patch' | 'document_delete_section' | 'document_restore',
  args: Record<string, unknown>,
) => void

interface Props {
  note: NoteDocument | null
  revisions: NoteRevision[]
  recent: Map<string, DocumentOp>
  last: DocumentUpdated | null
  canAct: boolean // connected and no turn running (the server answers BUSY otherwise)
  onAction?: NoteAction // absent on a shared page: read only
}

// The committee note (M4, act 2): the sections the agent wrote, the revisions, going back.
// Every change goes through the engine: deleting a section opens the confirmation modal.
export function NotePanel({ note, revisions, recent, last, canAct, onAction }: Props) {
  if (note === null) return <p className="muted">Chargement de la note…</p>

  return (
    <div className="note-panel">
      <header className="note-head">
        <h3>{note.title}</h3>
        <span className="chip chip-small">Révision {note.revision}</span>
        {onAction !== undefined && (
          <a className="btn-small" href={EXPORT_URL} download>
            Exporter (.md)
          </a>
        )}
      </header>
      {onAction !== undefined && last !== null && (
        <p className="note-last" role="status">
          {last.summary}
        </p>
      )}
      {isBlank(note) && onAction !== undefined && (
        <p className="muted small">
          La note est vide. Demandez à l'agent de la remplir, par exemple : « Mets à jour la section
          Rentabilité de la note du comité. »
        </p>
      )}
      <ol className="note-sections">
        {note.sections.map((section) => (
          <SectionCard
            key={section.section_id}
            section={section}
            revision={note.revision}
            change={recent.get(section.section_id) ?? null}
            canAct={canAct}
            onAction={onAction}
          />
        ))}
      </ol>
      {onAction !== undefined && (
        <History note={note} revisions={revisions} canAct={canAct} onAction={onAction} />
      )}
    </div>
  )
}

interface SectionProps {
  section: NoteSection
  revision: number
  change: DocumentOp | null
  canAct: boolean
  onAction?: NoteAction
}

function SectionCard({ section, revision, change, canAct, onAction }: SectionProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const fieldId = `note-edit-${section.section_id}`

  const save = (event: FormEvent) => {
    event.preventDefault()
    if (draft === null || onAction === undefined) return
    if (draft !== section.content) {
      // base_revision: refused (REVISION_CONFLICT) if the agent wrote in the meantime
      onAction('document_patch', {
        section_id: section.section_id,
        op: 'replace',
        content: draft,
        base_revision: revision,
      })
    }
    setDraft(null)
  }

  return (
    <li className={`note-section${change ? ' note-recent' : ''}`}>
      <div className="note-section-head">
        <h4>{section.title}</h4>
        {change && <span className="chip chip-small chip-up">{CHANGE_LABEL[change]}</span>}
      </div>
      {draft === null ? (
        section.content.trim() === '' ? (
          <p className="muted small">Section vide.</p>
        ) : (
          <div className="note-content">
            <Markdown remarkPlugins={[remarkGfm]}>{section.content}</Markdown>
          </div>
        )
      ) : (
        <form className="note-edit" onSubmit={save}>
          <label htmlFor={fieldId}>Contenu (Markdown, 4 000 caractères au plus)</label>
          <textarea
            id={fieldId}
            rows={6}
            maxLength={4000}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="note-actions">
            <button type="submit" className="btn-small" disabled={!canAct}>
              Enregistrer
            </button>
            <button type="button" className="btn-small" onClick={() => setDraft(null)}>
              Annuler
            </button>
          </div>
        </form>
      )}
      {draft === null && onAction !== undefined && (
        <div className="note-actions">
          <button
            type="button"
            className="btn-small"
            disabled={!canAct}
            onClick={() => setDraft(section.content)}
          >
            Modifier
          </button>
          <button
            type="button"
            className="btn-small note-delete"
            disabled={!canAct}
            onClick={() =>
              onAction('document_delete_section', {
                section_id: section.section_id,
                base_revision: revision,
              })
            }
          >
            Supprimer
          </button>
        </div>
      )}
    </li>
  )
}

interface HistoryProps {
  note: NoteDocument
  revisions: NoteRevision[]
  canAct: boolean
  onAction: NoteAction
}

// Newest first; going back writes a new revision and erases nothing (memory.md §7.1)
function History({ note, revisions, canAct, onAction }: HistoryProps) {
  if (revisions.length === 0) return null
  const restore = (revision: number) => onAction('document_restore', { revision })
  return (
    <section className="note-history">
      <h3>
        Révisions <span className="muted">· {revisions.length}</span>
      </h3>
      <ul>
        {[...revisions].reverse().map((revision) => (
          <li key={revision.revision}>
            <span className="note-revision">
              <strong>R{revision.revision}</strong> {revisionLabel(revision, note)}
              <span className="muted"> · {formatRevisionAt(revision.at)}</span>
            </span>
            {revision.revision < note.revision && (
              <button
                type="button"
                className="btn-small"
                disabled={!canAct}
                onClick={() => restore(revision.revision)}
              >
                Revenir ici
              </button>
            )}
          </li>
        ))}
        <li>
          <span className="note-revision">
            <strong>R0</strong> Note vierge
          </span>
          <button type="button" className="btn-small" disabled={!canAct} onClick={() => restore(0)}>
            Revenir ici
          </button>
        </li>
      </ul>
    </section>
  )
}
