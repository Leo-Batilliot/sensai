import { useEffect, useState } from 'react'
import type { ModelInfo } from './status'

// Installed Ollama models (GET /api/models), read again at each (re)connection
export function useModels(connected: boolean): ModelInfo[] {
  const [models, setModels] = useState<ModelInfo[]>([])

  useEffect(() => {
    if (!connected) return
    const controller = new AbortController()
    fetch('/api/models', { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((body: { models?: ModelInfo[] } | null) => {
        if (Array.isArray(body?.models)) setModels(body.models)
      })
      .catch(() => {
        // Ollama down or page closing: the selector keeps the current model only
      })
    return () => controller.abort()
  }, [connected])

  return models
}
