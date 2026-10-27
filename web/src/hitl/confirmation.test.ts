import { describe, expect, it } from 'vitest'
import type { ConfirmationResolved, EngineEvent, Impact } from '../protocol'
import { fixture, turnsOf, until } from '../test/fixtures'
import {
  bytesLabel,
  confirmLabel,
  confirmationOutcome,
  formatCountdown,
  formatDelta,
  impactLines,
  pendingConfirmation,
} from './confirmation'

const plain = (text: string) => text.replace(/\s/g, ' ')

const isRequired = (event: EngineEvent) => event.type === 'confirmation_required'
const isResolved = (event: EngineEvent) => event.type === 'confirmation_resolved'

// hitl_reject.jsonl with another resolution: what the engine sends on timeout, cancel…
function resolvedAs(patch: Partial<ConfirmationResolved>): EngineEvent[] {
  const events = fixture('hitl_reject.jsonl')
  return until(events, isResolved).map((event) =>
    event.type === 'confirmation_resolved' ? { ...event, ...patch } : event,
  )
}

describe('pendingConfirmation', () => {
  it('opens the modal on confirmation_required and closes it on confirmation_resolved', () => {
    const events = fixture('hitl_approve.jsonl')
    expect(pendingConfirmation(turnsOf(until(events, isRequired)))?.action_id).toBe('a-1')
    expect(pendingConfirmation(turnsOf(until(events, isResolved)))).toBeNull()
    expect(pendingConfirmation(turnsOf(events))).toBeNull()
  })

  it('never opens for a turn without confirmation', () => {
    expect(pendingConfirmation(turnsOf(fixture('nominal.jsonl')))).toBeNull()
    expect(pendingConfirmation([])).toBeNull()
  })
})

describe('confirmationOutcome', () => {
  it('approve: running until the tool result, then its summary', () => {
    const events = fixture('hitl_approve.jsonl')
    const [running] = turnsOf(until(events, isResolved))
    expect(confirmationOutcome(running)).toMatchObject({ final: false, title: 'Action confirmée' })
    const [done] = turnsOf(events)
    expect(confirmationOutcome(done)).toMatchObject({
      final: true,
      tone: 'ok',
      detail: '27 clients et 41 contacts supprimés',
    })
  })

  it('reject by the user keeps the comment', () => {
    const [turn] = turnsOf(fixture('hitl_reject.jsonl'))
    expect(confirmationOutcome(turn)).toMatchObject({
      final: true,
      title: 'Action refusée',
      comment: 'Pas avant la clôture.',
    })
  })

  it('says why the engine refused without a click', () => {
    const titles = [
      { decision: 'timeout', reason: 'timeout' },
      { decision: 'reject', reason: 'cancelled' },
      { decision: 'reject', reason: 'disconnected' },
    ].map((patch) => confirmationOutcome(turnsOf(resolvedAs(patch as Partial<ConfirmationResolved>))[0])?.title)
    expect(titles).toEqual(['Délai dépassé', 'Action annulée', 'Action refusée automatiquement'])
  })

  it('gives a new key to each state, for one toast per change', () => {
    const events = fixture('hitl_approve.jsonl')
    const keys = [until(events, isResolved), events].map(
      (part) => confirmationOutcome(turnsOf(part)[0])?.key,
    )
    expect(new Set(keys).size).toBe(2)
  })

  it('is null before the decision and for a turn without confirmation', () => {
    expect(confirmationOutcome(turnsOf(until(fixture('hitl_reject.jsonl'), isRequired))[0])).toBeNull()
    expect(confirmationOutcome(turnsOf(fixture('nominal.jsonl'))[0])).toBeNull()
  })
})

describe('impact', () => {
  const impact = (patch: Partial<Impact>): Impact => ({
    operation: 'DELETE',
    rows_affected: 27,
    per_table: { contacts: -41, clients: -27 },
    sample: [],
    blocked: [],
    ...patch,
  })

  it('lists the tables, biggest change first, with a real minus sign', () => {
    expect(impactLines(impact({}))).toEqual([
      { table: 'contacts', delta: -41 },
      { table: 'clients', delta: -27 },
    ])
    expect(formatDelta(-27)).toBe('−27')
    expect(plain(formatDelta(1200))).toBe('+1 200')
  })

  it('has no table line for an UPDATE: rows_affected is shown instead', () => {
    expect(impactLines(impact({ operation: 'UPDATE', per_table: { clients: 0 } }))).toEqual([])
  })

  it('repeats what will happen on the confirm button', () => {
    expect(confirmLabel(impact({}))).toBe('Confirmer la suppression de 27 lignes')
    expect(confirmLabel(impact({ operation: 'UPDATE', rows_affected: 1 }))).toBe(
      'Confirmer la modification de 1 ligne',
    )
    expect(confirmLabel(impact({ rows_affected: 0, per_table: {} }))).toBe('Confirmer')
  })

  describe('file write (FILE-01, act 5)', () => {
    const file = { path: 'reports/note.md', bytes: 2841, exists: false, excerpt: '# Note' }
    const write = (patch: Partial<Impact>) =>
      impact({ operation: 'OTHER', rows_affected: 0, per_table: {}, file, ...patch })

    it('says whether the file is created or replaced', () => {
      expect(confirmLabel(write({}))).toBe('Confirmer la création du fichier')
      expect(confirmLabel(write({ file: { ...file, exists: true } }))).toBe(
        'Confirmer le remplacement du fichier',
      )
    })

    it('promises nothing when the permissions refuse the path', () => {
      const blocked = [{ reason: 'PATH_FORBIDDEN : hors des dossiers autorisés', count: null }]
      expect(confirmLabel(write({ blocked }))).toBe('Confirmer')
    })

    it('shows the size in octets, then in ko', () => {
      expect(bytesLabel(1)).toBe('1 octet')
      expect(bytesLabel(636)).toBe('636 octets')
      expect(bytesLabel(2841)).toBe('2,8 ko')
    })
  })
})

describe('formatCountdown', () => {
  it('shows minutes and seconds, rounded up, never below zero', () => {
    expect(formatCountdown(120)).toBe('2:00')
    expect(formatCountdown(106.2)).toBe('1:47')
    expect(formatCountdown(0.4)).toBe('0:01')
    expect(formatCountdown(-3)).toBe('0:00')
  })
})
