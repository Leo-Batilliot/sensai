import { describe, expect, it } from 'vitest'
import type { EngineEvent, Persona, PersonaChanged, ServerMessage, SessionReady } from '../protocol'
import { FIXTURE_FILES, fixture, until } from '../test/fixtures'
import { SESSION_LOST, chatReducer, initialState, isBusy, type ChatState } from './reducer'

const ANALYSTE: Persona = { id: 'analyste', name: 'Contrôle de gestion', role: 'Copilote de Claire' }
const ADV: Persona = { id: 'adv', name: 'ADV', role: 'Copilote de Karim' }

function ready(
  history: SessionReady['history'] = [],
  protocolVersion = 1,
  persona: Persona = ANALYSTE,
): SessionReady {
  return {
    type: 'session_ready',
    session_id: 's-fixture',
    ts: 0,
    protocol_version: protocolVersion,
    server_version: 'test',
    resumed: history.length > 0,
    history,
    model: 'qwen3:8b',
    supports_tools: true,
    ctx_max: 8192,
    config: { temperature: 0.1, num_ctx: 8192, judge_enabled: true, judge_model: null },
    servers: [],
    tools: [],
    persona,
    personas: [ANALYSTE, ADV],
    document_id: null,
  }
}

function personaChanged(persona: Persona): PersonaChanged {
  return { type: 'persona_changed', session_id: 's-fixture', ts: 0, persona, tools: [] }
}

function play(messages: ServerMessage[], state: ChatState = initialState): ChatState {
  return chatReducer(state, { type: 'server', messages })
}

// One dispatch per message, as when the frames are not batched
function playOneByOne(messages: ServerMessage[]): ChatState {
  return messages.reduce((state, message) => play([message], state), play([ready()]))
}


describe('replaying the fixtures', () => {
  it.each(FIXTURE_FILES)('%s: the text shown is the final answer of each turn', (name) => {
    const events = fixture(name)
    const state = play([ready(), ...events])
    const done = events.filter((event) => event.type === 'message_done')
    for (const event of done) {
      const turn = state.turns.find((candidate) => candidate.id === event.turn_id)
      expect(turn?.content).toBe(event.content)
    }
    expect(isBusy(state)).toBe(false)
  })

  it('act 1: question, tool calls, answer, usage and judge', () => {
    const state = play([ready(), ...fixture('nominal.jsonl')])
    const [turn] = state.turns
    expect(state.turns).toHaveLength(1)
    expect(turn.userMessage).toContain('5 clients les plus rentables')
    expect(turn.status).toBe('done')
    expect(turn.thinking).not.toBe('')
    expect(turn.steps.map((step) => step.tool)).toContain('sql_toolbox__run_select')
    expect(turn.steps.every((step) => step.result !== null)).toBe(true)
    expect(turn.judge).toMatchObject({ badge: 'verified' })
    expect(state.lastUsage?.ctx_max).toBe(8192)
  })

  it('batched and one-by-one dispatches give the same state', () => {
    const events = fixture('nominal.jsonl')
    expect(play([ready(), ...events]).turns).toEqual(playOneByOne(events).turns)
  })
})

describe('event types added after this version (events.md v1.1, rule 7)', () => {
  // What the engine starts to emit with the v1.1 extensions (contracts/v1.1-extensions.md)
  function withNewTypes(events: EngineEvent[]): ServerMessage[] {
    const started = events.findIndex((event) => event.type === 'turn_started')
    const turnId = events[started].turn_id
    const common = { session_id: 's-fixture', turn_id: turnId, ts: 0 }
    const unknown = [
      { type: 'context_compacted', ...common, turns: 3, tokens_before: 9000, tokens_after: 4000, summary: '…' },
      { type: 'examples_injected', ...common, examples: [{ id: 'ex-1', question: 'Top 5 ?', score: 0.9 }] },
    ] as unknown as ServerMessage[]
    // and a new message of the web layer, outside any turn
    const outside = { type: 'persona_changed', session_id: 's-fixture', ts: 0, persona: 'cfo' }
    return [
      ...events.slice(0, started + 1),
      ...unknown,
      outside as unknown as ServerMessage,
      ...events.slice(started + 1),
    ]
  }

  it('ignores them: the turn ends exactly as without them', () => {
    const events = fixture('nominal.jsonl')
    const expected = play([ready(), ...events])
    const state = play([ready(), ...withNewTypes(events)])
    expect(state.turns).toEqual(expected.turns)
    expect(state.notices).toEqual([])
    expect(isBusy(state)).toBe(false)
  })

  it('ignores them one message at a time as well', () => {
    const state = playOneByOne(withNewTypes(fixture('hitl_approve.jsonl')))
    expect(state.turns).toHaveLength(1)
    expect(state.turns[0].status).toBe('done')
  })
})

describe('turn status', () => {
  it('is busy while running and while waiting for a confirmation', () => {
    const events = fixture('hitl_reject.jsonl')
    const pause = events.findIndex((event) => event.type === 'confirmation_required')
    const started = play([ready(), events[0]])
    expect(isBusy(started)).toBe(true)
    const waiting = play([ready(), ...events.slice(0, pause + 1)])
    expect(waiting.turns[0].status).toBe('awaiting_confirmation')
    expect(isBusy(waiting)).toBe(true)
    expect(isBusy(play([ready(), ...events]))).toBe(false)
  })

  it('shows the tool being called, then clears it', () => {
    const events = fixture('nominal.jsonl')
    const call = events.findIndex((event) => event.type === 'tool_call')
    expect(play([ready(), ...events.slice(0, call + 1)]).turns[0].currentTool).toBe(
      'time__get_current_time',
    )
    expect(play([ready(), ...events.slice(0, call + 2)]).turns[0].currentTool).toBeNull()
  })

  it('an interrupted answer keeps its text and the previous context gauge', () => {
    const before = play([ready(), ...fixture('nominal.jsonl')])
    const turnId = 't-cancel'
    const common = { session_id: 's-fixture', turn_id: turnId, ts: 1 }
    const state = play(
      [
        { ...common, type: 'turn_started', origin: 'user', user_message: 'Bonjour' },
        { ...common, type: 'token', delta: 'Voici le dé' },
        {
          ...common,
          type: 'message_done',
          content: 'Voici le dé',
          interrupted: true,
          usage: null,
          duration_ms: 300,
        },
      ],
      before,
    )
    const turn = state.turns[1]
    expect(turn.status).toBe('interrupted')
    expect(turn.content).toBe('Voici le dé')
    expect(state.lastUsage).toEqual(before.lastUsage)
  })

  it('a turn ended by an error is failed and shows the message', () => {
    const state = play([ready(), ...fixture('errors.jsonl')])
    expect(state.turns[0].status).toBe('failed')
    expect(state.turns[0].error?.code).toBe('OLLAMA_UNAVAILABLE')
  })
})

describe('session messages', () => {
  it('a resumed session rebuilds the same turns from its history, without tokens', () => {
    const events = fixture('memory.jsonl')
    const live = play([ready(), ...events])
    const history = events.filter((event) => event.type !== 'token' && event.type !== 'thinking')
    const resumed = play([ready(history)])
    expect(resumed.turns.map((turn) => [turn.content, turn.status])).toEqual(
      live.turns.map((turn) => [turn.content, turn.status]),
    )
    expect(resumed.turns[1].context[0].label).toBe('client rentable')
  })

  it('a reset empties the conversation and the gauge, then ignores late events', () => {
    const events = fixture('nominal.jsonl')
    const judge = events.filter((event) => event.type === 'judge_report')
    const before = play([ready(), ...events.filter((event) => event.type !== 'judge_report')])
    const after = play([{ type: 'session_reset', session_id: 's-fixture', ts: 2 }, ...judge], before)
    expect(after.turns).toEqual([])
    expect(after.lastUsage).toBeNull()
  })

  it('errors outside a turn become notices, three at most', () => {
    const busy = (n: number): EngineEvent => ({
      type: 'error',
      session_id: 's-fixture',
      turn_id: null,
      ts: n,
      code: 'BUSY',
      message: 'Une réponse est déjà en cours.',
      recoverable: true,
      reply_to: `c-${n}`,
    })
    const state = play([ready(), busy(1), busy(2), busy(3), busy(4)])
    expect(state.notices.map((notice) => notice.id)).toEqual([2, 3, 4])
    expect(state.turns).toEqual([])
    const dismissed = chatReducer(state, { type: 'dismiss_notice', id: 3 })
    expect(dismissed.notices.map((notice) => notice.id)).toEqual([2, 4])
  })

  it('says so when a reconnection finds a server that lost the conversation', () => {
    const before = play([ready(), ...fixture('nominal.jsonl')])
    const restarted = play([{ ...ready(), resumed: false }], before)
    expect(restarted.turns).toEqual([])
    expect(restarted.notices.map((notice) => [notice.code, notice.message])).toEqual([
      ['SESSION_LOST', SESSION_LOST],
    ])
    const history = fixture('nominal.jsonl').filter((event) => event.type !== 'token')
    expect(play([ready(history)], before).notices).toEqual([]) // resumed: nothing lost
    expect(play([ready()]).notices).toEqual([]) // first connection
  })

  it('flags a server that speaks another protocol version', () => {
    expect(play([ready([], 2)]).incompatible).toBe(true)
    expect(play([ready()]).incompatible).toBe(false)
  })

  it('applies a model change and a config change', () => {
    const state = play([
      ready(),
      { type: 'model_changed', session_id: 's-fixture', ts: 1, model: 'gemma2:9b', supports_tools: false, ctx_max: 8192 },
      {
        type: 'config_updated',
        session_id: 's-fixture',
        ts: 2,
        config: { temperature: 0.2, num_ctx: 16384, judge_enabled: false, judge_model: null },
        changed: ['temperature', 'num_ctx', 'judge_enabled'],
      },
    ])
    expect(state.session).toMatchObject({ model: 'gemma2:9b', supportsTools: false, ctxMax: 16384 })
  })
})

describe('profile change (A5, WEB-15)', () => {
  const nominal = fixture('nominal.jsonl')

  it('updates the status bar and puts a separator after the turns before it', () => {
    const state = play([ready(), ...nominal, personaChanged(ADV)])
    expect(state.session?.persona).toEqual(ADV)
    expect(state.personaMarks).toEqual([{ afterTurns: 1, persona: ADV }])
    expect(state.turns).toHaveLength(1) // the conversation of Claire stays (US-A5-01)
  })

  it('during a turn, the separator goes after the running turn', () => {
    const running = until(nominal, (event) => event.type === 'tool_call')
    const state = play([ready(), ...running, personaChanged(ADV)])
    expect(isBusy(state)).toBe(true)
    expect(state.personaMarks[0].afterTurns).toBe(1)
  })

  it('asking for the current profile again adds no separator', () => {
    const state = play([ready(), ...nominal, personaChanged(ANALYSTE)])
    expect(state.personaMarks).toEqual([])
  })

  it('a reload replays the separator in place, even when the profile is already the new one', () => {
    const live = play([ready(), ...nominal, personaChanged(ADV)])
    const reloaded = play([ready([...nominal, personaChanged(ADV)], 1, ADV)], live)
    expect(reloaded.personaMarks).toEqual(live.personaMarks)
    expect(reloaded.session?.persona).toEqual(ADV)
  })

  it('a reset removes the separators and keeps the profile', () => {
    const state = play([
      ready(),
      ...nominal,
      personaChanged(ADV),
      { type: 'session_reset', session_id: 's-fixture', ts: 0 },
    ])
    expect(state.personaMarks).toEqual([])
    expect(state.session?.persona).toEqual(ADV)
  })
})
