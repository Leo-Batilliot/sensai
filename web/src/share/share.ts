// Export and read-only sharing of a conversation (X3, websocket.md §4.10 and §9)

import type { SharedSnapshot } from '../protocol'
import { chatReducer, initialState, type ChatState } from '../session/reducer'

export type ExportFormat = 'md' | 'json'

export function exportUrl(sessionId: string, format: ExportFormat): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`
}

// "/share/<token>": the page of a shared link, null for the main screen
export function shareToken(pathname: string): string | null {
  const match = /^\/share\/([A-Za-z0-9_-]{16,64})\/?$/.exec(pathname)
  return match === null ? null : match[1]
}

// The snapshot replayed by the same reducer as a live conversation (websocket.md principle 4)
export function sharedState(snapshot: SharedSnapshot): ChatState {
  return chatReducer(initialState, { type: 'server', messages: snapshot.events })
}

const when = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' })

export function formatSharedAt(createdAt: number): string {
  return when.format(new Date(createdAt * 1000))
}
