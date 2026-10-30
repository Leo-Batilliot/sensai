import { useEffect, useState } from 'react'
import type { ScheduleInfo } from '../protocol'

// GET /api/schedules, read again when a run starts or ends (`version`) and on reconnection.
// null until the first answer: the panel says it is loading, not that there is no task.
export function useSchedules(connected: boolean, version: number): ScheduleInfo[] | null {
  const [schedules, setSchedules] = useState<ScheduleInfo[] | null>(null)

  useEffect(() => {
    if (!connected) return
    const controller = new AbortController()
    fetch('/api/schedules', { signal: controller.signal })
      .then((response) =>
        response.ok ? (response.json() as Promise<{ schedules: ScheduleInfo[] }>) : null,
      )
      .then((body) => {
        if (body !== null) setSchedules(body.schedules)
      })
      .catch(() => {
        // Server restarting or page closing: the panel keeps what it showed
      })
    return () => controller.abort()
  }, [connected, version])

  return schedules
}
