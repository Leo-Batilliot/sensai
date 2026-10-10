// Conversation state, rebuilt from the server messages only (websocket.md principle 4):
// live events and the history of a resumed session go through the same function.

import {
  PROTOCOL_VERSION,
  type ConfirmationRequired,
  type ConfirmationResolved,
  type ContextInjected,
  type EngineError,
  type EngineEvent,
  type JudgeReport,
  type MemoryUpdated,
  type ServerInfo,
  type ServerMessage,
  type SessionConfig,
  type SqlGenerated,
  type ToolInfo,
  type ToolResult,
  type Usage,
} from '../protocol'

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'replaced'

export type TurnStatus = 'running' | 'awaiting_confirmation' | 'done' | 'interrupted' | 'failed'

export interface ToolStep {
  callId: string
  server: string | null
  tool: string
  arguments: Record<string, unknown>
  sql: SqlGenerated | null
  result: ToolResult | null
}

export interface Turn {
  id: string
  origin: 'user' | 'ui'
  userMessage: string | null
  content: string
  thinking: string
  status: TurnStatus
  currentTool: string | null // tool called and not finished yet: "Appelle X…"
  steps: ToolStep[]
  context: ContextInjected['refs']
  confirmation: ConfirmationRequired | null
  resolution: ConfirmationResolved | null
  memoryUpdates: MemoryUpdated[]
  error: EngineError | null
  usage: Usage | null
  durationMs: number | null
  judge: 'pending' | JudgeReport | null
}

export interface SessionInfo {
  model: string
  supportsTools: boolean
  ctxMax: number
  config: SessionConfig
  servers: ServerInfo[]
  tools: ToolInfo[]
}

export interface Notice {
  id: number
  code: string
  message: string
}

export interface ChatState {
  connection: ConnectionStatus
  session: SessionInfo | null
  turns: Turn[]
  lastUsage: Usage | null // context gauge: keeps its last value when usage is null
  notices: Notice[] // errors outside any turn (BUSY, INVALID_MESSAGE…)
  incompatible: boolean // server speaks another protocol version
  nextNoticeId: number
}

export type ChatAction =
  | { type: 'connection'; status: ConnectionStatus }
  | { type: 'server'; messages: ServerMessage[] }
  | { type: 'dismiss_notice'; id: number }

export const initialState: ChatState = {
  connection: 'connecting',
  session: null,
  turns: [],
  lastUsage: null,
  notices: [],
  incompatible: false,
  nextNoticeId: 1,
}

const MAX_NOTICES = 3

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'connection':
      return { ...state, connection: action.status }
    case 'server':
      return action.messages.reduce(applyMessage, state)
    case 'dismiss_notice':
      return { ...state, notices: state.notices.filter((notice) => notice.id !== action.id) }
  }
}

export function isBusy(state: ChatState): boolean {
  const last = state.turns[state.turns.length - 1]
  return last !== undefined && (last.status === 'running' || last.status === 'awaiting_confirmation')
}

function applyMessage(state: ChatState, message: ServerMessage): ChatState {
  switch (message.type) {
    case 'session_ready': {
      const replayed = message.history.reduce(applyEvent, { ...state, turns: [], lastUsage: null })
      return {
        ...replayed,
        connection: 'open',
        incompatible: message.protocol_version !== PROTOCOL_VERSION,
        session: {
          model: message.model,
          supportsTools: message.supports_tools,
          ctxMax: message.ctx_max,
          config: message.config,
          servers: message.servers,
          tools: message.tools,
        },
      }
    }
    case 'session_reset':
      return { ...state, turns: [], lastUsage: null } // the gauge shows "—" until the next answer
    case 'model_changed':
      return updateSession(state, {
        model: message.model,
        supportsTools: message.supports_tools,
        ctxMax: message.ctx_max,
      })
    case 'config_updated':
      return updateSession(state, { config: message.config, ctxMax: message.config.num_ctx })
    default:
      return applyEvent(state, message)
  }
}

function updateSession(state: ChatState, patch: Partial<SessionInfo>): ChatState {
  return state.session === null ? state : { ...state, session: { ...state.session, ...patch } }
}

function applyEvent(state: ChatState, event: EngineEvent): ChatState {
  if (event.type === 'turn_started') {
    if (state.turns.some((turn) => turn.id === event.turn_id)) return state
    return { ...state, turns: [...state.turns, newTurn(event.turn_id, event.origin, event.user_message)] }
  }
  if (event.type === 'error' && event.turn_id === null) {
    const notice = { id: state.nextNoticeId, code: event.code, message: event.message }
    return {
      ...state,
      notices: [...state.notices, notice].slice(-MAX_NOTICES),
      nextNoticeId: state.nextNoticeId + 1,
    }
  }
  const index = state.turns.findIndex((turn) => turn.id === event.turn_id)
  if (index === -1) return state // late event of a turn erased by a reset
  const turn = applyToTurn(state.turns[index], event)
  const turns = state.turns.slice()
  turns[index] = turn
  const lastUsage = event.type === 'message_done' && event.usage !== null ? event.usage : state.lastUsage
  return { ...state, turns, lastUsage }
}

function newTurn(id: string, origin: Turn['origin'], userMessage: string | null): Turn {
  return {
    id,
    origin,
    userMessage,
    content: '',
    thinking: '',
    status: 'running',
    currentTool: null,
    steps: [],
    context: [],
    confirmation: null,
    resolution: null,
    memoryUpdates: [],
    error: null,
    usage: null,
    durationMs: null,
    judge: null,
  }
}

function applyToTurn(turn: Turn, event: EngineEvent): Turn {
  switch (event.type) {
    case 'token':
      return { ...turn, content: turn.content + event.delta, currentTool: null }
    case 'thinking':
      return { ...turn, thinking: turn.thinking + event.delta }
    case 'context_injected':
      return { ...turn, context: event.refs }
    case 'tool_call':
      return {
        ...turn,
        currentTool: event.tool,
        steps: [
          ...turn.steps,
          {
            callId: event.call_id,
            server: event.server,
            tool: event.tool,
            arguments: event.arguments,
            sql: null,
            result: null,
          },
        ],
      }
    case 'sql_generated':
      return updateStep(turn, event.call_id, { sql: event })
    case 'tool_result':
      return { ...updateStep(turn, event.call_id, { result: event }), currentTool: null }
    case 'confirmation_required':
      return { ...turn, confirmation: event, status: 'awaiting_confirmation' }
    case 'confirmation_resolved':
      return { ...turn, resolution: event, status: 'running' }
    case 'memory_updated':
      return { ...turn, memoryUpdates: [...turn.memoryUpdates, event] }
    case 'message_done':
      return {
        ...turn,
        content: event.content, // authoritative: equal to the tokens, also when replayed
        status: event.interrupted ? 'interrupted' : 'done',
        currentTool: null,
        usage: event.usage,
        durationMs: event.duration_ms,
      }
    case 'error':
      return { ...turn, status: 'failed', currentTool: null, error: event }
    case 'judge_started':
      return { ...turn, judge: 'pending' }
    case 'judge_report':
      return { ...turn, judge: event }
    case 'turn_started':
      return turn
  }
}

function updateStep(turn: Turn, callId: string, patch: Partial<ToolStep>): Turn {
  return {
    ...turn,
    steps: turn.steps.map((step) => (step.callId === callId ? { ...step, ...patch } : step)),
  }
}
