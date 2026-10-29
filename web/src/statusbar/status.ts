// What the status bar says about the model, the MCP servers and the context window (WEB-10).

import type { ServerInfo } from '../protocol'

export interface ModelInfo {
  name: string
  size_bytes: number // 0 when the server does not know it
  supports_tools: boolean
}

export type GaugeTone = 'ok' | 'warn' | 'over'

export interface Gauge {
  percent: number // can go past 100: the model then forgets the start of the prompt
  tone: GaugeTone
}

const WARN_RATIO = 0.75

export function gauge(used: number, max: number): Gauge {
  const ratio = max > 0 ? used / max : 0
  return {
    percent: Math.round(ratio * 100),
    tone: ratio > 1 ? 'over' : ratio >= WARN_RATIO ? 'warn' : 'ok',
  }
}

export const GAUGE_HINT: Record<GaugeTone, string> = {
  ok: 'Tokens utilisés par le dernier appel au modèle, sur la taille de sa fenêtre de contexte.',
  warn: 'La fenêtre de contexte est presque pleine : une réponse longue ou une conversation qui continue peut la dépasser.',
  over:
    "La fenêtre de contexte est dépassée : le modèle a oublié le début de la conversation, consignes et outils compris. Commencez une nouvelle conversation ou agrandissez la fenêtre de contexte (Réglages).",
}

export function serversUp(servers: ServerInfo[]): number {
  return servers.filter((server) => server.status === 'up').length
}

const gigabytes = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })

// "qwen3:8b · 5,2 Go", "llama3.2 · sans outils"
export function modelLabel(model: ModelInfo): string {
  const parts = [model.name]
  if (model.size_bytes > 0) parts.push(`${gigabytes.format(model.size_bytes / 1e9)} Go`)
  if (!model.supports_tools) parts.push('sans outils')
  return parts.join(' · ')
}

// The current model stays in the list even if Ollama did not return it (yet)
export function modelChoices(models: ModelInfo[], current: string | null): ModelInfo[] {
  if (current === null || models.some((model) => model.name === current)) return models
  return [{ name: current, size_bytes: 0, supports_tools: true }, ...models]
}
