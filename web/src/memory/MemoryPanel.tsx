import { useState, type FormEvent, type ReactNode } from 'react'
import type { MemoryKind } from '../protocol'
import {
  ENTITY_TYPE,
  FACT_CATEGORY,
  entityName,
  entryKey,
  isEmpty,
  parseAliases,
  type MemorySnapshot,
} from './memory'

type UiAction = (tool: 'memory_update' | 'memory_delete', args: Record<string, unknown>) => void

interface Props {
  memory: MemorySnapshot
  recent: Map<string, 'create' | 'update'>
  canAct: boolean // connected and no turn running (the server answers BUSY otherwise)
  onAction: UiAction
}

// What the agent has learned (docs/keynote/ui/PanneauMemoire). Editing and deleting go
// through the engine: a deletion opens the confirmation modal, like any destructive tool.
export function MemoryPanel({ memory, recent, canAct, onAction }: Props) {
  if (isEmpty(memory)) {
    return (
      <div className="memory-empty">
        <p className="muted">L'agent n'a encore rien retenu.</p>
        <p className="muted small">
          Apprenez-lui une règle de l'entreprise, par exemple : « Chez nous, un client rentable c'est
          une marge brute supérieure à 25 %. Retiens-le. »
        </p>
      </div>
    )
  }

  const entry = (kind: MemoryKind, ref: string, view: ReactNode, edit: EditForm | null) => (
    <MemoryEntry
      key={entryKey(kind, ref)}
      kind={kind}
      memoryRef={ref}
      change={recent.get(entryKey(kind, ref)) ?? null}
      view={view}
      edit={edit}
      canAct={canAct}
      onAction={onAction}
    />
  )

  return (
    <div className="memory-panel">
      <Section title="Glossaire" count={memory.glossary.length}>
        {memory.glossary.map((term) =>
          entry(
            'glossary',
            term.ref,
            <>
              <strong>{term.term}</strong>
              <span>{term.definition}</span>
              {term.sql_hint && <code className="memory-sql">{term.sql_hint}</code>}
            </>,
            { label: 'Définition', field: 'definition', value: term.definition, multiline: true },
          ),
        )}
      </Section>
      <Section title="Entités" count={memory.entities.length}>
        {memory.entities.map((entity) =>
          entry(
            'entity',
            entity.ref,
            <>
              <strong>{entity.name}</strong>
              <span className="muted small">
                {ENTITY_TYPE[entity.type] ?? entity.type}
                {entity.aliases.length > 0 && ` · alias : ${entity.aliases.join(', ')}`}
                {entity.external_ref && ` · ${entity.external_ref}`}
              </span>
            </>,
            {
              label: 'Alias (séparés par des virgules)',
              field: 'aliases',
              value: entity.aliases.join(', '),
              multiline: false,
            },
          ),
        )}
      </Section>
      <Section title="Relations" count={memory.relations.length}>
        {memory.relations.map((relation) =>
          entry(
            'relation',
            relation.ref,
            <span>
              {entityName(memory, relation.src)} <code>{relation.type}</code>{' '}
              {entityName(memory, relation.dst)}
            </span>,
            null,
          ),
        )}
      </Section>
      <Section title="Faits" count={memory.facts.length}>
        {memory.facts.map((fact) =>
          entry(
            'fact',
            fact.ref,
            <>
              <span>{fact.content}</span>
              <span className="muted small">
                {FACT_CATEGORY[fact.category] ?? fact.category}
                {fact.entity !== null && ` · ${entityName(memory, fact.entity)}`}
                {` · retenu ${fact.source === 'user' ? 'à votre demande' : "par l'agent"}`}
              </span>
            </>,
            { label: 'Contenu', field: 'content', value: fact.content, multiline: true },
          ),
        )}
      </Section>
    </div>
  )
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  if (count === 0) return null
  return (
    <section className="memory-section">
      <h3>
        {title} <span className="muted">· {count}</span>
      </h3>
      <ul>{children}</ul>
    </section>
  )
}

interface EditForm {
  label: string
  field: string
  value: string
  multiline: boolean
}

interface EntryProps {
  kind: MemoryKind
  memoryRef: string
  change: 'create' | 'update' | null
  view: ReactNode
  edit: EditForm | null
  canAct: boolean
  onAction: UiAction
}

function MemoryEntry({ kind, memoryRef, change, view, edit, canAct, onAction }: EntryProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const fieldId = `memory-edit-${kind}-${memoryRef}`

  const save = (event: FormEvent) => {
    event.preventDefault()
    if (edit === null || draft === null) return
    const value = edit.field === 'aliases' ? parseAliases(draft) : draft.trim()
    if (value !== '' && draft !== edit.value) {
      onAction('memory_update', { kind, ref: memoryRef, patch: { [edit.field]: value } })
    }
    setDraft(null)
  }

  return (
    <li className={`memory-entry${change ? ' memory-recent' : ''}`}>
      {change && <span className="chip chip-small chip-up">{change === 'create' ? 'nouveau' : 'modifié'}</span>}
      {draft === null || edit === null ? (
        <div className="memory-view">{view}</div>
      ) : (
        <form className="memory-edit" onSubmit={save}>
          <label htmlFor={fieldId}>{edit.label}</label>
          {edit.multiline ? (
            <textarea id={fieldId} rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          ) : (
            <input id={fieldId} value={draft} onChange={(e) => setDraft(e.target.value)} />
          )}
          <div className="memory-actions">
            <button type="submit" className="btn-small" disabled={!canAct}>
              Enregistrer
            </button>
            <button type="button" className="btn-small" onClick={() => setDraft(null)}>
              Annuler
            </button>
          </div>
        </form>
      )}
      {draft === null && (
        <div className="memory-actions">
          {edit !== null && (
            <button
              type="button"
              className="btn-small"
              disabled={!canAct}
              onClick={() => setDraft(edit.value)}
            >
              Modifier
            </button>
          )}
          <button
            type="button"
            className="btn-small memory-delete"
            disabled={!canAct}
            onClick={() => onAction('memory_delete', { kind, ref: memoryRef })}
          >
            Supprimer
          </button>
        </div>
      )}
    </li>
  )
}
