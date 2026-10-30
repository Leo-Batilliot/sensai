import { useState } from 'react'
import type { ConfigView, SessionConfig } from '../protocol'
import { useMenu } from '../shared/useMenu'
import { contextChoices, formatTemperature, readOnlyRows, resetPatch } from './settings'
import { modelChoices } from './status'
import { useConfigView } from './useConfigView'
import { useModels } from './useModels'

const numbers = new Intl.NumberFormat('fr-FR')

interface Props {
  config: SessionConfig | null
  model: string | null
  connected: boolean
  onSetConfig: (patch: Partial<SessionConfig>) => void
}

// Every setting set_config accepts (websocket.md §4.7), for this session only and from the
// next question; config.toml is never written. The rest of config.toml is shown, read only.
export function SettingsMenu({ config, model, connected, onSetConfig }: Props) {
  const menu = useMenu()
  const view = useConfigView(connected)
  const reset = view !== null && config !== null ? resetPatch(view.defaults, config) : {}
  const changed = Object.keys(reset).length > 0

  return (
    <details className="settings-menu" {...menu.props}>
      {/* A dot rather than a word when changed: the status bar has no room left at 1280 px */}
      <summary
        className={`chip${changed ? ' chip-warn' : ''}`}
        title={changed ? 'Réglages modifiés pour cette conversation' : undefined}
      >
        Réglages
        {changed && (
          <>
            <span className="settings-dot" aria-hidden="true" />
            <span className="sr-only"> (modifiés)</span>
          </>
        )}
      </summary>
      <div className="settings-popover">
        {config === null || view === null ? (
          <p className="muted">Réglages indisponibles : connexion au serveur…</p>
        ) : (
          <SettingsForm
            config={config}
            view={view}
            model={model}
            connected={connected}
            menuOpen={menu.open}
            reset={reset}
            onSetConfig={onSetConfig}
          />
        )}
      </div>
    </details>
  )
}

interface FormProps {
  config: SessionConfig
  view: ConfigView
  model: string | null
  connected: boolean
  menuOpen: boolean
  reset: Partial<SessionConfig>
  onSetConfig: (patch: Partial<SessionConfig>) => void
}

function SettingsForm({ config, view, model, connected, menuOpen, reset, onSetConfig }: FormProps) {
  const models = modelChoices(useModels(connected && menuOpen), config.judge_model)
  const defaultContext = view.defaults.num_ctx
  return (
    <>
      <p className="settings-hint muted">
        Pour cette conversation, à partir de la prochaine question. <code>config.toml</code>{' '}
        n'est pas modifié.
      </p>

      <TemperatureField
        key={config.temperature} // the confirmed value from the server starts a new draft
        value={config.temperature}
        bounds={view.bounds.temperature}
        disabled={!connected}
        onCommit={(temperature) => onSetConfig({ temperature })}
      />

      <label className="setting">
        <span className="setting-label">Fenêtre de contexte</span>
        <select
          value={config.num_ctx}
          disabled={!connected}
          onChange={(event) => onSetConfig({ num_ctx: Number(event.target.value) })}
        >
          {contextChoices(view.bounds.num_ctx, config.num_ctx).map((size) => (
            <option key={size} value={size}>
              {numbers.format(size)} jetons{size === defaultContext && ' (config.toml)'}
            </option>
          ))}
        </select>
        <small className="muted">
          Plus grande : l'agent garde plus de conversation, mais le modèle est rechargé (la
          prochaine réponse est plus lente) et prend plus de mémoire.
        </small>
      </label>

      <label className="setting setting-check">
        <input
          type="checkbox"
          checked={config.judge_enabled}
          disabled={!connected}
          onChange={(event) => onSetConfig({ judge_enabled: event.target.checked })}
        />
        <span className="setting-label">Vérifier les réponses (juge)</span>
      </label>

      <label className="setting">
        <span className="setting-label">Modèle du juge</span>
        <select
          value={config.judge_model ?? ''}
          disabled={!connected || !config.judge_enabled}
          onChange={(event) => onSetConfig({ judge_model: event.target.value || null })}
        >
          <option value="">Même modèle que l'agent{model !== null && ` (${model})`}</option>
          {models.map((choice) => (
            <option key={choice.name} value={choice.name}>
              {choice.name}
            </option>
          ))}
        </select>
      </label>

      <div className="settings-actions">
        <button
          type="button"
          className="btn-small"
          disabled={!connected || Object.keys(reset).length === 0}
          onClick={() => onSetConfig(reset)}
        >
          Réinitialiser
        </button>
      </div>

      <details className="settings-fixed">
        <summary>Fixés dans config.toml</summary>
        <dl>
          {readOnlyRows(view).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </details>
    </>
  )
}

interface TemperatureProps {
  value: number
  bounds: [number, number]
  disabled: boolean
  onCommit: (value: number) => void
}

// Sent when the slider is released, not at every step of a drag
function TemperatureField({ value, bounds, disabled, onCommit }: TemperatureProps) {
  const [draft, setDraft] = useState(value)
  const commit = () => {
    if (draft !== value) onCommit(draft)
  }
  return (
    <label className="setting">
      <span className="setting-label">
        Température <output>{formatTemperature(draft)}</output>
      </span>
      <input
        type="range"
        min={bounds[0]}
        max={bounds[1]}
        step={0.05}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(Number(event.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <small className="muted">0 : réponses stables d'une fois à l'autre ; 1 : plus variées.</small>
    </label>
  )
}
