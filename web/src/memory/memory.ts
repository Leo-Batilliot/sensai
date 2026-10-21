// The agent's memory as GET /api/memory returns it (docs/contracts/memory.md §5).

import type { MemoryKind } from '../protocol'
import type { Turn } from '../session/reducer'

export interface GlossaryEntry {
  ref: string
  term: string
  definition: string
  sql_hint: string | null
  updated_at: string
}

export interface EntityEntry {
  ref: string
  type: string
  name: string
  aliases: string[]
  external_ref: string | null
  attributes: Record<string, unknown>
  updated_at: string
}

export interface RelationEntry {
  ref: string
  src: string
  type: string
  dst: string
  attributes: Record<string, unknown>
  updated_at: string
}

export interface FactEntry {
  ref: string
  entity: string | null
  category: string
  content: string
  source: string
  updated_at: string
}

export interface MemorySnapshot {
  glossary: GlossaryEntry[]
  entities: EntityEntry[]
  relations: RelationEntry[]
  facts: FactEntry[]
}

export const EMPTY_MEMORY: MemorySnapshot = { glossary: [], entities: [], relations: [], facts: [] }

export const ENTITY_TYPE: Record<string, string> = {
  client: 'client',
  person: 'personne',
  product: 'produit',
  metric: 'indicateur',
  team: 'équipe',
  other: 'autre',
}

export const FACT_CATEGORY: Record<string, string> = {
  preference: 'préférence',
  business_rule: 'règle métier',
  note: 'note',
}

export function isEmpty(memory: MemorySnapshot): boolean {
  return Object.values(memory).every((entries) => entries.length === 0)
}

export function entityName(memory: MemorySnapshot, ref: string | null): string | null {
  if (ref === null) return null
  return memory.entities.find((entity) => entity.ref === ref)?.name ?? `entité ${ref}`
}

// Number of memory writes seen so far: the panel reads GET /api/memory again when it moves
export function memoryVersion(turns: Turn[]): number {
  return turns.reduce((total, turn) => total + turn.memoryUpdates.length, 0)
}

// Entries written by the last turn that wrote anything: shown as "nouveau" or "modifié"
export function recentChanges(turns: Turn[]): Map<string, 'create' | 'update'> {
  const last = [...turns].reverse().find((turn) => turn.memoryUpdates.length > 0)
  const changes = new Map<string, 'create' | 'update'>()
  for (const update of last?.memoryUpdates ?? []) {
    if (update.op !== 'delete') changes.set(entryKey(update.kind, update.ref), update.op)
  }
  return changes
}

export function entryKey(kind: MemoryKind, ref: string): string {
  return `${kind}:${ref}`
}

// "Lemaire, BL" → ["Lemaire", "BL"]
export function parseAliases(text: string): string[] {
  return text
    .split(',')
    .map((alias) => alias.trim())
    .filter((alias) => alias !== '')
}
