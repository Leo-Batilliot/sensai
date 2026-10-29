import type { Persona, ServerInfo, SessionConfig, Usage } from '../protocol'
import type { ChatState, ConnectionStatus } from '../session/reducer'
import { useMenu } from '../shared/useMenu'
import { SettingsMenu } from './SettingsMenu'
import { GAUGE_HINT, gauge, modelChoices, modelLabel, serversUp } from './status'
import { useModels } from './useModels'

const CONNECTION: Record<ConnectionStatus, { label: string; tone: string }> = {
  connecting: { label: 'Connexion…', tone: '' },
  open: { label: 'Connecté', tone: 'chip-up' },
  reconnecting: { label: 'Reconnexion…', tone: 'chip-down' },
  replaced: { label: 'Ouverte ailleurs', tone: 'chip-down' },
}

const numbers = new Intl.NumberFormat('fr-FR')

interface Props {
  state: ChatState
  connected: boolean
  onSetModel: (model: string) => void
  onSetConfig: (patch: Partial<SessionConfig>) => void
  onSetPersona: (persona: string) => void
}

// Status bar (docs/keynote/ui/BarreEtat): profile, model, MCP servers, context window,
// settings, connection
export function StatusBar({ state, connected, onSetModel, onSetConfig, onSetPersona }: Props) {
  const connection = CONNECTION[state.connection]
  const session = state.session

  return (
    <header className="statusbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          S
        </span>
        <strong>SENSAI</strong>
        <span className="muted">Nordvent Distribution</span>
      </div>
      <div className="statusbar-items">
        <PersonaSelect
          current={session?.persona ?? null}
          personas={session?.personas ?? []}
          connected={connected}
          onSetPersona={onSetPersona}
        />
        <ModelSelect
          current={session?.model ?? null}
          supportsTools={session?.supportsTools ?? true}
          connected={connected}
          onSetModel={onSetModel}
        />
        <ServersMenu servers={session?.servers ?? []} />
        <ContextGauge usage={state.lastUsage} ctxMax={session?.ctxMax ?? null} />
        <SettingsMenu
          config={session?.config ?? null}
          model={session?.model ?? null}
          connected={connected}
          onSetConfig={onSetConfig}
        />
        <span className={`chip ${connection.tone}`} role="status">
          {connection.label}
        </span>
      </div>
    </header>
  )
}

interface PersonaSelectProps {
  current: Persona | null
  personas: Persona[]
  connected: boolean
  onSetPersona: (persona: string) => void
}

// The profile applies from the next turn and the conversation is kept (websocket.md §4.9).
// Hidden for a server without profiles.
function PersonaSelect({ current, personas, connected, onSetPersona }: PersonaSelectProps) {
  if (current === null || personas.length === 0) return null
  return (
    <label className="chip chip-select persona-select" title={current.role}>
      <span>Profil :</span>
      <select
        id="persona-select"
        value={current.id}
        disabled={!connected}
        onChange={(event) => onSetPersona(event.target.value)}
        title="Le nouveau profil répond à partir de la prochaine question ; la conversation est conservée. Un profil retire des outils, il ne supprime jamais une confirmation."
      >
        {personas.map((persona) => (
          <option key={persona.id} value={persona.id}>
            {persona.name}
          </option>
        ))}
      </select>
    </label>
  )
}

interface ModelSelectProps {
  current: string | null
  supportsTools: boolean
  connected: boolean
  onSetModel: (model: string) => void
}

// The change applies from the next turn; the history is kept (websocket.md §4.6)
function ModelSelect({ current, supportsTools, connected, onSetModel }: ModelSelectProps) {
  const models = modelChoices(useModels(connected), current)
  return (
    <>
      <label className="chip chip-select model-select">
        <span>Modèle :</span>
        <select
          id="model-select"
          value={current ?? ''}
          disabled={!connected || current === null}
          onChange={(event) => onSetModel(event.target.value)}
          title="Le nouveau modèle répond à partir de la prochaine question ; la conversation est conservée."
        >
          {current === null && <option value="">—</option>}
          {models.map((model) => (
            <option key={model.name} value={model.name}>
              {modelLabel(model)}
            </option>
          ))}
        </select>
      </label>
      {current !== null && !supportsTools && (
        <span
          className="chip chip-warn"
          title="Ce modèle ne sait pas appeler d'outils : pas de requête SQL, pas de mémoire. Choisissez un modèle compatible."
        >
          ! sans outils
        </span>
      )}
    </>
  )
}

// Which MCP servers answer, and why one does not (e.g. a missing uvx command)
function ServersMenu({ servers }: { servers: ServerInfo[] }) {
  const menu = useMenu()
  const up = serversUp(servers)
  const tone = servers.length > 0 && up < servers.length ? ' chip-warn' : ''
  return (
    <details className="servers-menu" {...menu.props}>
      <summary className={`chip${tone}`}>
        Serveurs MCP : {up}/{servers.length}
      </summary>
      <ul className="servers-list">
        {servers.length === 0 && <li className="muted">Aucun serveur MCP configuré.</li>}
        {servers.map((server) => (
          <li key={server.name}>
            <span className={`server-dot ${server.status}`} aria-hidden="true" />
            <span className="server-name">{server.name}</span>
            <span className={server.status === 'up' ? 'server-up' : 'server-down'}>
              {server.status === 'up' ? 'en ligne' : 'indisponible'}
            </span>
            {server.status === 'down' && server.error && (
              <span className="server-error">{server.error}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}

// Tokens of the last model call over the context window: past 100 %, the start is lost.
// The window is the session's: a size changed in Réglages shows at once, not after a turn.
function ContextGauge({ usage, ctxMax }: { usage: Usage | null; ctxMax: number | null }) {
  if (usage === null) {
    return (
      <span className="chip" title={GAUGE_HINT.ok}>
        Contexte : — {ctxMax !== null && `/ ${numbers.format(ctxMax)}`}
      </span>
    )
  }
  const max = ctxMax ?? usage.ctx_max
  const { percent, tone } = gauge(usage.ctx_used, max)
  return (
    <span className={`chip context-gauge gauge-${tone}`} title={GAUGE_HINT[tone]}>
      <span>
        Contexte : {numbers.format(usage.ctx_used)} / {numbers.format(max)}
        {tone === 'over' && ' · dépassé'}
      </span>
      <span
        className="gauge-bar"
        role="meter"
        aria-label="Fenêtre de contexte utilisée"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(percent, 100)}
        aria-valuetext={`${percent} %`}
      >
        <span style={{ width: `${Math.min(percent, 100)}%` }} />
      </span>
    </span>
  )
}
