import { useState } from 'react'
import { formatDuration, shortToolName, type QueryStep, type StepStatus } from '../session/steps'
import { CopyButton } from '../shared/CopyButton'
import { SqlCode } from './SqlCode'

interface Props {
  step: QueryStep
  status: StepStatus
  resultId: string | null // id of the results table, when there is one
}

// Query run by the agent (docs/keynote/ui/BlocSQL): folded on one line by default
export function SqlBlock({ step, status, resultId }: Props) {
  const [open, setOpen] = useState(false)
  const { sql, operation } = step.sql
  const write = operation !== 'SELECT'

  const showResults = () => {
    if (resultId === null) return
    const table = document.getElementById(resultId)
    table?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    table?.focus({ preventScroll: true })
  }

  return (
    <section className={`sql-block${write ? ' sql-write' : ''}`} aria-label="Requête SQL">
      <div className="sql-header">
        <button
          type="button"
          className="sql-toggle"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <span aria-hidden="true">{open ? '▼' : '▶'}</span> SQL
        </button>
        <span className={`chip chip-small${write ? ' chip-down' : ''}`}>
          {write ? `écriture · ${operation}` : 'lecture seule'}
        </span>
        {status === 'confirmation' && (
          <span className="chip chip-small chip-warn">en attente de confirmation</span>
        )}
        {open ? (
          <span className="sql-meta muted">
            {step.server ?? 'outil'} · {shortToolName(step)}
            {step.result !== null && ` · ${formatDuration(step.result.duration_ms)}`}
            {step.result !== null && ` · ${step.result.summary}`}
          </span>
        ) : (
          <code className="sql-preview">{sql}</code>
        )}
        <span className="sql-actions">
          <CopyButton text={sql} />
          {resultId !== null && (
            <button type="button" className="btn-small" onClick={showResults}>
              ↓ Voir les résultats
            </button>
          )}
        </span>
      </div>
      {open && <SqlCode sql={sql} />}
    </section>
  )
}
