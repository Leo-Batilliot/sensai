import { useId, useState } from 'react'
import type { Claim, JudgeReport } from '../protocol'
import { formatDuration } from '../session/steps'
import { badgeView } from './judge'

const VERDICT: Record<Claim['verdict'], { icon: string; label: string }> = {
  supported: { icon: '✓', label: 'vérifiée' },
  contradicted: { icon: '✗', label: 'contredite' },
  unverifiable: { icon: '!', label: 'non vérifiable' },
}

const percent = new Intl.NumberFormat('fr-FR', { style: 'percent', maximumFractionDigits: 0 })

// Verdict of the judge on one answer (docs/keynote/ui/BadgeJuge), with its detail folded
export function JudgeBadge({ judge }: { judge: 'pending' | JudgeReport }) {
  const [open, setOpen] = useState(false)
  const detailId = useId()
  const view = badgeView(judge)
  const content = (
    <>
      <span aria-hidden="true">{view.icon}</span> {view.label}
    </>
  )

  if (judge === 'pending') {
    return (
      <span className="judge-badge tone-pending" role="status">
        {content}
      </span>
    )
  }

  // A fragment: in the message header the detail takes a full line under the badge
  return (
    <>
      <button
        type="button"
        className={`judge-badge tone-${view.tone}`}
        title={view.hint ?? undefined}
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setOpen(!open)}
      >
        {content} <span aria-hidden="true">{open ? '▲' : '▼'}</span>
      </button>
      {open && <JudgeDetail id={detailId} judge={judge} hint={view.hint} />}
    </>
  )
}

function JudgeDetail({ id, judge, hint }: { id: string; judge: JudgeReport; hint: string | null }) {
  const { report, scores, numeric_check: numeric } = judge
  const numericText =
    numeric.status === 'ok'
      ? '✓ tous les chiffres retrouvés'
      : numeric.status === 'mismatch'
        ? `✗ absents des données : ${numeric.unmatched.join(', ')}`
        : `! résultat tronqué, non retrouvés : ${numeric.unmatched.join(', ')}`

  return (
    <div className="judge-detail" id={id}>
      {report === null ? (
        <p className="muted">{hint}</p>
      ) : report.claims.length === 0 ? (
        <p className="muted">Aucune affirmation chiffrée à vérifier.</p>
      ) : (
        <ul className="claims">
          {report.claims.map((claim, index) => (
            <li key={index} className={`claim-row claim-${claim.verdict}`}>
              <span className="claim-icon" aria-hidden="true">
                {VERDICT[claim.verdict].icon}
              </span>
              <span className="sr-only">{VERDICT[claim.verdict].label} :</span>
              <span>
                {claim.text.replace(/\*\*|`/g, '')}
                {claim.expected_value !== null && (
                  <strong> → attendu {claim.expected_value}</strong>
                )}
                <span className="claim-evidence muted"> · {claim.evidence}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <dl className="judge-metrics">
        {report !== null && (
          <>
            <div>
              <dt>Pertinence</dt>
              <dd>{report.relevance}/5</dd>
            </div>
            <div>
              <dt>Le SQL répond à la question</dt>
              <dd>{report.sql_answers_question ? 'oui' : 'non'}</dd>
            </div>
          </>
        )}
        <div>
          <dt>Fidélité</dt>
          <dd>{scores.faithfulness === null ? '—' : percent.format(scores.faithfulness)}</dd>
        </div>
        <div>
          <dt>Contrôle numérique</dt>
          <dd>{numericText}</dd>
        </div>
        <div>
          <dt>Durée</dt>
          <dd>{formatDuration(judge.duration_ms)}</dd>
        </div>
      </dl>
    </div>
  )
}
