import type { JudgeReportBody } from '../protocol'
import { formatFigure } from './judge'

interface Props {
  report: JudgeReportBody
  canAsk: boolean
  onAsk: (question: string) => void
}

// Key figures, assumptions and follow-ups of the judge's report (docs/keynote/ui/FicheIndicateurs)
export function KeyFigures({ report, canAsk, onAsk }: Props) {
  const { key_figures: figures, assumptions, followups } = report
  if (figures.length === 0 && assumptions.length === 0 && followups.length === 0) return null

  return (
    <section className="key-figures" aria-label="Chiffres clés">
      {figures.length > 0 && (
        <ul className="kpi-cards">
          {figures.map((figure, index) => (
            <li key={index} className="kpi">
              <span className="kpi-label">{figure.label}</span>
              <strong className="kpi-value">{formatFigure(figure.value, figure.unit)}</strong>
              <span className={`kpi-source${figure.source_column === null ? ' computed' : ''}`}>
                {figure.source_column === null ? (
                  '! valeur calculée, sans colonne source'
                ) : (
                  <>
                    ✓ colonne <code>{figure.source_column}</code>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {assumptions.length > 0 && (
        <div className="kpi-notes">
          <h3>Hypothèses</h3>
          <ul>
            {assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </div>
      )}
      {followups.length > 0 && (
        <div className="followups">
          <h3>Relances</h3>
          <div className="followup-buttons">
            {followups.map((question) => (
              <button
                key={question}
                type="button"
                className="example"
                disabled={!canAsk}
                onClick={() => onAsk(question)}
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
