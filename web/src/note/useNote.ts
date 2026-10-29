import { useEffect, useState } from 'react'
import type { NoteDocument, NoteRevision } from '../protocol'

export interface NoteState {
  note: NoteDocument | null // null until the first answer
  revisions: NoteRevision[]
}

// GET /api/documents/current and its revisions, read again after each note call
// (`version`) and each reconnection
export function useNote(connected: boolean, version: number): NoteState {
  const [state, setState] = useState<NoteState>({ note: null, revisions: [] })

  useEffect(() => {
    if (!connected) return
    const controller = new AbortController()
    const read = <T,>(url: string) =>
      fetch(url, { signal: controller.signal }).then((response) =>
        response.ok ? (response.json() as Promise<T>) : null,
      )
    Promise.all([
      read<NoteDocument>('/api/documents/current'),
      read<{ revisions: NoteRevision[] }>('/api/documents/current/revisions'),
    ])
      .then(([note, history]) => {
        if (note !== null) setState({ note, revisions: history?.revisions ?? [] })
      })
      .catch(() => {
        // Server restarting or page closing: the panel keeps what it showed
      })
    return () => controller.abort()
  }, [connected, version])

  return state
}
