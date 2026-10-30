import type { ConfigView, SessionConfig } from '../protocol'

const numbers = new Intl.NumberFormat('fr-FR')

// Context sizes offered: the powers of two within the bounds, plus the current value when
// config.toml sets another one
export function contextChoices(bounds: [number, number], current: number): number[] {
  const choices = new Set([current])
  for (let size = 1024; size <= bounds[1]; size *= 2) {
    if (size >= bounds[0]) choices.add(size)
  }
  return [...choices].sort((a, b) => a - b)
}

// The settings that differ from config.toml: what « Réinitialiser » sends back (§4.7)
export function resetPatch(defaults: SessionConfig, config: SessionConfig): Partial<SessionConfig> {
  const keys = Object.keys(defaults) as (keyof SessionConfig)[]
  const changed = keys.filter((key) => defaults[key] !== config[key])
  return Object.fromEntries(changed.map((key) => [key, defaults[key]])) as Partial<SessionConfig>
}

export function formatTemperature(value: number): string {
  return value.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// The settings of config.toml shown but never changed from the interface, in French
export function readOnlyRows(view: ConfigView): [string, string][] {
  const r = view.read_only
  return [
    ['Serveur Ollama', r.ollama_host],
    ['Modèle gardé chargé', r.keep_alive],
    ["Appels d'outils par question", numbers.format(r.max_tool_iterations)],
    ['Corrections SQL', numbers.format(r.max_sql_retries)],
    ['Lignes par requête', numbers.format(r.row_limit)],
    ["Délai d'une requête", `${numbers.format(r.query_timeout_ms / 1000)} s`],
    ['Délai de confirmation', `${numbers.format(r.confirmation_timeout_s)} s`],
    ['Tolérance du juge sur les chiffres', `${numbers.format(r.numeric_tolerance * 100)} %`],
  ]
}
