import { useEffect, useState } from 'react'
import { EMPTY_MEMORY, type MemorySnapshot } from './memory'

// GET /api/memory, read again after each memory write (`version`) and each reconnection
export function useMemory(connected: boolean, version: number): MemorySnapshot {
  const [memory, setMemory] = useState<MemorySnapshot>(EMPTY_MEMORY)

  useEffect(() => {
    if (!connected) return
    const controller = new AbortController()
    fetch('/api/memory', { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<MemorySnapshot>) : null))
      .then((body) => {
        if (body !== null) setMemory({ ...EMPTY_MEMORY, ...body })
      })
      .catch(() => {
        // Server restarting or page closing: the panel keeps what it showed
      })
    return () => controller.abort()
  }, [connected, version])

  return memory
}
