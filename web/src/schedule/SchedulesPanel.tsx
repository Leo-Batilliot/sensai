import type { Persona, ScheduleInfo, ScheduleRun } from '../protocol'
import {
  formatWhen,
  isRunning,
  reportUrl,
  runView,
  scheduleTitle,
  timetableLabel,
  triggerLabel,
} from './schedule'

interface Props {
  schedules: ScheduleInfo[] | null
  personas: Persona[]
  connected: boolean
  onRun: (scheduleId: string) => void
}

// The tasks of [schedules] in config.toml (X5, act 5): when they run, "Exécuter
// maintenant", and the last runs with their badge and report.
export function SchedulesPanel({ schedules, personas, connected, onRun }: Props) {
  if (schedules === null) {
    return <p className="muted">Chargement des tâches planifiées…</p>
  }
  if (schedules.length === 0) {
    return (
      <div className="schedules-empty">
        <p className="muted">Aucune tâche planifiée.</p>
        <p className="muted schedule-note">
          Un administrateur les déclare dans <code>config.toml</code>, section{' '}
          <code>[schedules.&lt;id&gt;]</code> : une question, une heure, un profil.
        </p>
      </div>
    )
  }

  return (
    <div className="schedules-panel">
      <p className="schedule-note muted">
        Ces tâches tournent sans humain : elles ne peuvent rien modifier, toute écriture serait
        refusée.
      </p>
      <ul className="schedule-list">
        {schedules.map((task) => (
          <ScheduleCard
            key={task.id}
            task={task}
            persona={personas.find((persona) => persona.id === task.persona)?.name ?? task.persona}
            connected={connected}
            onRun={onRun}
          />
        ))}
      </ul>
    </div>
  )
}

interface CardProps {
  task: ScheduleInfo
  persona: string | null
  connected: boolean
  onRun: (scheduleId: string) => void
}

function ScheduleCard({ task, persona, connected, onRun }: CardProps) {
  const running = isRunning(task)
  const titleId = `schedule-${task.id}`
  return (
    <li className={`schedule-card${task.enabled ? '' : ' schedule-disabled'}`} aria-labelledby={titleId}>
      <div className="schedule-head">
        <strong id={titleId}>{scheduleTitle(task.id)}</strong>
        <span className="chip chip-small">{timetableLabel(task)}</span>
      </div>
      <p className="schedule-question">« {task.question} »</p>
      <p className="schedule-note muted">
        {task.enabled
          ? task.next_run === null
            ? 'Prochaine exécution inconnue'
            : `Prochaine exécution : ${formatWhen(task.next_run)}`
          : 'Désactivée dans config.toml'}
        {persona !== null && ` · profil ${persona}`}
      </p>
      {task.enabled && (
        <button
          type="button"
          className="btn-small schedule-run"
          disabled={!connected || running}
          onClick={() => onRun(task.id)}
        >
          {running ? 'Exécution en cours…' : 'Exécuter maintenant'}
        </button>
      )}
      {task.last_runs.length > 0 && (
        <div className="schedule-runs">
          <h4>Dernières exécutions</h4>
          <ul>
            {task.last_runs.map((run) => (
              <RunRow key={run.run_id} run={run} />
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}

function RunRow({ run }: { run: ScheduleRun }) {
  const view = runView(run)
  return (
    <li className="schedule-run-row">
      <span>
        {formatWhen(run.started_at)} <span className="muted">· {triggerLabel(run.trigger)}</span>
      </span>
      <span className="schedule-run-result">
        <span className={`judge-badge tone-${view.tone}`} role={run.status === null ? 'status' : undefined}>
          <span aria-hidden="true">{view.icon}</span> {view.label}
        </span>
        {run.report_path !== null && (
          <a href={reportUrl(run.run_id)} target="_blank" rel="noreferrer" title={run.report_path}>
            Rapport
          </a>
        )}
      </span>
      {run.error !== null && <span className="schedule-error">{run.error.message}</span>}
    </li>
  )
}
