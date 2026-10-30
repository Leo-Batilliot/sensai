import { useEffect, useState } from 'react'
import type { ConfigView } from '../protocol'

// GET /api/config: the defaults and bounds of the settings, read at each (re)connection
export function useConfigView(connected: boolean): ConfigView | null {
  const [view, setView] = useState<ConfigView | null>(null)

  useEffect(() => {
    if (!connected) return
    const controller = new AbortController()
    fetch('/api/config', { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<ConfigView>) : null))
      .then((body) => {
        if (body !== null) setView(body)
      })
      .catch(() => {
        // Server restarting or page closing: the menu keeps what it showed
      })
    return () => controller.abort()
  }, [connected])

  return view
}
