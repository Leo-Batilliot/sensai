import { describe, expect, it } from 'vitest'
import type { EngineEvent, ServerMessage, SessionReady } from '../protocol'
import { FIXTURE_FILES, fixture } from '../test/fixtures'
import { chatReducer, initialState, isBusy, type ChatState } from './reducer'

function ready(history: EngineEvent[] = [], protocolVersion = 1): SessionReady {
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
  }
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
