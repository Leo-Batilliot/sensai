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

// Name of the PDF: browsers name the file after the page title, « SENSAI » alone otherwise
export function pdfTitle(date: Date): string {
  const day = (n: number) => String(n).padStart(2, '0')
  return `SENSAI - conversation du ${date.getFullYear()}-${day(date.getMonth() + 1)}-${day(date.getDate())}`
}

// PDF export: the browser's print window (« Enregistrer au format PDF »), with the print
// styles of share.css. Called after a menu closes: print at the next frame, menu gone.
export function printPage(): void {
  const title = document.title
  document.title = pdfTitle(new Date())
  window.addEventListener('afterprint', () => (document.title = title), { once: true })
  window.requestAnimationFrame(() => window.print())
}
