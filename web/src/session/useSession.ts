// Connection to WS /ws/{session_id} (websocket.md §2) and the actions of the interface.

import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import type { ClientMessage, ServerMessage } from '../protocol'
import { chatReducer, initialState, isBusy, type ChatState } from './reducer'

const STORAGE_KEY = 'sensai.session_id'
const RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000] // then 5 s between attempts
const CLOSE_NORMAL = 1000
const CLOSE_INVALID_SESSION = 4400
const CLOSE_REPLACED = 4409

export interface Session {
  state: ChatState
  busy: boolean
  connected: boolean
  sendMessage: (content: string) => void
  cancel: () => void
  reset: () => void
  confirm: (actionId: string, decision: 'approve' | 'reject', comment?: string) => void
  setModel: (model: string) => void
  uiAction: (tool: 'memory_update' | 'memory_delete', args: Record<string, unknown>) => void
  dismissNotice: (id: number) => void
}

export function useSession(): Session {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const socketRef = useRef<WebSocket | null>(null)
  const nextId = useRef(1)

  useEffect(() => {
    // StrictMode mounts twice in dev: the first socket is closed by the cleanup, and
    // `disposed` keeps its close handler from scheduling a reconnection.
    let disposed = false
    let attempt = 0
    let retryTimer: number | undefined
    let frame: number | null = null
    let pending: ServerMessage[] = []
    let sessionId = storedSessionId()

    // One render per animation frame, however many tokens arrived meanwhile
    const flush = () => {
      frame = null
      const messages = pending
      pending = []
      if (messages.length > 0) dispatch({ type: 'server', messages })
    }

    const connect = () => {
      const socket = new WebSocket(socketUrl(sessionId))
      socketRef.current = socket
      socket.onopen = () => {
        attempt = 0
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          pending.push(JSON.parse(event.data) as ServerMessage)
        } catch {
          return // not JSON: ignored, the server never sends that
        }
        if (frame === null) frame = requestAnimationFrame(flush)
      }
      socket.onclose = (event) => {
        if (socketRef.current === socket) socketRef.current = null
        if (disposed) return
        if (frame !== null) cancelAnimationFrame(frame)
        flush()
        if (event.code === CLOSE_REPLACED) {
          dispatch({ type: 'connection', status: 'replaced' }) // another tab took the session
          return
        }
        if (event.code === CLOSE_INVALID_SESSION) sessionId = renewSessionId()
        dispatch({ type: 'connection', status: 'reconnecting' })
        const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)]
        attempt += 1
        retryTimer = window.setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(retryTimer)
      if (frame !== null) cancelAnimationFrame(frame)
      socketRef.current?.close(CLOSE_NORMAL)
      socketRef.current = null
    }
  }, [])

  const send = useCallback((message: ClientMessage) => {
    const socket = socketRef.current
    if (socket === null || socket.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ ...message, id: `c-${nextId.current++}` }))
  }, [])

  const busy = isBusy(state)
  const connected = state.connection === 'open'

  return useMemo(
    () => ({
      state,
      busy,
      connected,
      sendMessage: (content: string) => {
        if (content.trim() !== '') send({ type: 'user_message', content })
      },
      cancel: () => send({ type: 'cancel' }),
      reset: () => send({ type: 'reset' }),
      confirm: (actionId, decision, comment) =>
        send({ type: 'confirm', action_id: actionId, decision, comment }),
      setModel: (model) => send({ type: 'set_model', model }),
      uiAction: (tool, args) => send({ type: 'ui_action', tool, arguments: args }),
      dismissNotice: (id) => dispatch({ type: 'dismiss_notice', id }),
    }),
    [state, busy, connected, send],
  )
}

function socketUrl(sessionId: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/ws/${sessionId}`
}

// One session per tab: sessionStorage survives a reload, a new tab starts a new session
function storedSessionId(): string {
  try {
    const stored = window.sessionStorage.getItem(STORAGE_KEY)
    if (stored !== null) return stored
  } catch {
    // storage blocked (private mode…): a session per page load
  }
  return renewSessionId()
}

function renewSessionId(): string {
  const id = newSessionId()
  try {
    window.sessionStorage.setItem(STORAGE_KEY, id)
  } catch {
    // see storedSessionId
  }
  return id
}

function newSessionId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  // randomUUID needs a secure context: fallback when opened over plain http on the network
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
