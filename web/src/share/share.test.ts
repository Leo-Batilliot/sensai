import { describe, expect, it } from 'vitest'
import type { PersonaChanged, ShareCreated, SharedSnapshot } from '../protocol'
import { chatReducer, initialState } from '../session/reducer'
import { fixture } from '../test/fixtures'
import { exportUrl, sharedState, shareToken } from './share'

const ADV = { id: 'adv', name: 'ADV', role: 'Copilote de Karim' }

describe('export and share (X3, EXPORT-01)', () => {
  it('builds the export link of the session', () => {
    expect(exportUrl('3f2b-1', 'md')).toBe('/api/sessions/3f2b-1/export?format=md')
    expect(exportUrl('3f2b-1', 'json')).toBe('/api/sessions/3f2b-1/export?format=json')
  })

  it('recognizes the page of a shared link, and only it', () => {
    expect(shareToken('/share/kq3Vb0wLr8nT2xYc5HdJ1A')).toBe('kq3Vb0wLr8nT2xYc5HdJ1A')
    expect(shareToken('/share/kq3Vb0wLr8nT2xYc5HdJ1A/')).toBe('kq3Vb0wLr8nT2xYc5HdJ1A')
    expect(shareToken('/')).toBeNull()
    expect(shareToken('/share/../api')).toBeNull()
    expect(shareToken('/share/short')).toBeNull()
  })

  it('replays a snapshot with the reducer of a live conversation', () => {
    const changed: PersonaChanged = { type: 'persona_changed', session_id: 's', ts: 0, persona: ADV, tools: [] }
    const snapshot: SharedSnapshot = {
      token: 'kq3Vb0wLr8nT2xYc5HdJ1A',
      url: 'http://127.0.0.1:8000/share/kq3Vb0wLr8nT2xYc5HdJ1A',
      created_at: 1760434000,
      model: 'qwen3:8b',
      persona: ADV,
      events: [...fixture('question.jsonl').filter((event) => event.type !== 'token'), changed],
      document: null,
    }
    const state = sharedState(snapshot)
    expect(state.turns).toHaveLength(1)
    expect(state.turns[0].status).toBe('done')
    expect(state.turns[0].answer?.option_id).toBe('historique')
    expect(state.personaMarks).toEqual([{ afterTurns: 1, persona: ADV }])
  })

  it('reads the list again after a share or a revocation', () => {
    const created: ShareCreated = {
      type: 'share_created',
      session_id: 's',
      ts: 0,
      token: 'kq3Vb0wLr8nT2xYc5HdJ1A',
      url: 'http://127.0.0.1:8000/share/kq3Vb0wLr8nT2xYc5HdJ1A',
      created_at: 0,
    }
    const state = chatReducer(initialState, {
      type: 'server',
      messages: [created, { type: 'share_revoked', session_id: 's', ts: 0, token: created.token }],
    })
    expect(state.sharesVersion).toBe(2)
  })
})
