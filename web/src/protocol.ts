// Types of what travels over the WebSocket. Source of truth:
// docs/contracts/events.md (engine events) and docs/contracts/websocket.md (the rest).

export type Decision = 'approve' | 'reject' | 'timeout'
export type DecisionReason = 'user' | 'cancelled' | 'disconnected' | 'timeout'
export type MemoryKind = 'glossary' | 'entity' | 'relation' | 'fact'
export type Badge = 'verified' | 'needs_review' | 'contradicted' | 'unavailable'

interface Common {
  session_id: string
  turn_id: string
  ts: number
}

// --- engine events (events.md §3) -------------------------------------------------------

export interface TurnStarted extends Common {
  type: 'turn_started'
  origin: 'user' | 'ui'
  user_message: string | null
}

export interface ContextInjected extends Common {
  type: 'context_injected'
  refs: { kind: MemoryKind; ref: string; label: string }[]
}

export interface Token extends Common {
  type: 'token'
  delta: string
}

export interface Thinking extends Common {
  type: 'thinking'
  delta: string
}

export interface ToolCall extends Common {
  type: 'tool_call'
  call_id: string
  server: string | null
  tool: string
  arguments: Record<string, unknown>
}

export interface SqlGenerated extends Common {
  type: 'sql_generated'
  call_id: string
  sql: string
  operation: 'SELECT' | 'INSERT' | 'UPDATE' | 'DELETE' | 'OTHER'
}

export interface Impact {
  operation: string
  rows_affected: number
  per_table: Record<string, number>
  sample: Record<string, unknown>[]
  blocked: { reason: string; count: number | null }[]
}

export interface ConfirmationRequired extends Common {
  type: 'confirmation_required'
  action_id: string
  call_id: string
  tool: string
  sql: string | null
  impact: Impact
  expires_in_s: number
}

export interface ConfirmationResolved extends Common {
  type: 'confirmation_resolved'
  action_id: string
  decision: Decision
  reason: DecisionReason
  comment: string | null
}

export interface ToolResult extends Common {
  type: 'tool_result'
  call_id: string
  ok: boolean
  summary: string
  data: Record<string, unknown> | null
  error: { code: string; message: string } | null
  duration_ms: number
}

export interface MemoryUpdated extends Common {
  type: 'memory_updated'
  op: 'create' | 'update' | 'delete'
  kind: MemoryKind
  ref: string
  summary: string
}

export interface Usage {
  prompt_tokens: number
  completion_tokens: number
  ctx_used: number
  ctx_max: number
}

export interface MessageDone extends Common {
  type: 'message_done'
  content: string
  interrupted: boolean
  usage: Usage | null
  duration_ms: number
}

export interface JudgeStarted extends Common {
  type: 'judge_started'
}

export interface JudgeReport extends Common {
  type: 'judge_report'
  badge: Badge
  scores: { faithfulness: number | null }
  numeric_check: { status: 'ok' | 'mismatch' | 'inconclusive'; unmatched: string[] }
  report: Record<string, unknown> | null // schemas/judge_report.v1.json, used by WEB-09
  duration_ms: number
}

export interface EngineError extends Omit<Common, 'turn_id'> {
  type: 'error'
  turn_id: string | null // null outside a turn (protocol errors)
  code: string
  message: string
  recoverable: boolean
  reply_to?: string
}

export type EngineEvent =
  | TurnStarted
  | ContextInjected
  | Token
  | Thinking
  | ToolCall
  | SqlGenerated
  | ConfirmationRequired
  | ConfirmationResolved
  | ToolResult
  | MemoryUpdated
  | MessageDone
  | JudgeStarted
  | JudgeReport
  | EngineError

// --- messages of the web layer (websocket.md §5.2) --------------------------------------

export interface SessionConfig {
  temperature: number
  num_ctx: number
  judge_enabled: boolean
  judge_model: string | null
}

export interface ServerInfo {
  name: string
  status: 'up' | 'down'
  error?: string | null
}

export interface ToolInfo {
  name: string
  server: string | null
  description: string
  read_only: boolean | null
  destructive: boolean | null
  requires_confirmation: boolean
}

interface WebCommon {
  session_id: string
  ts: number
  reply_to?: string
}

export interface SessionReady extends WebCommon {
  type: 'session_ready'
  protocol_version: number
  server_version: string
  resumed: boolean
  history: EngineEvent[]
  model: string
  supports_tools: boolean
  ctx_max: number
  config: SessionConfig
  servers: ServerInfo[]
  tools: ToolInfo[]
}

export interface SessionReset extends WebCommon {
  type: 'session_reset'
}

export interface ModelChanged extends WebCommon {
  type: 'model_changed'
  model: string
  supports_tools: boolean
  ctx_max: number
}

export interface ConfigUpdated extends WebCommon {
  type: 'config_updated'
  config: SessionConfig
  changed: (keyof SessionConfig)[]
}

export type ServerMessage = EngineEvent | SessionReady | SessionReset | ModelChanged | ConfigUpdated

export const PROTOCOL_VERSION = 1

// --- client messages (websocket.md §4) --------------------------------------------------

export type ClientMessage =
  | { type: 'user_message'; id?: string; content: string }
  | { type: 'ui_action'; id?: string; tool: string; arguments: Record<string, unknown> }
  | { type: 'confirm'; id?: string; action_id: string; decision: 'approve' | 'reject'; comment?: string }
  | { type: 'cancel'; id?: string }
  | { type: 'reset'; id?: string }
  | { type: 'set_model'; id?: string; model: string }
  | { type: 'set_config'; id?: string; patch: Partial<SessionConfig> }
