import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import type { ConfirmationRequired, FileImpact } from '../protocol'
import { columnFormat, formatCell, type Cell } from '../results/table'
import { SqlCode } from '../sql/SqlCode'
import {
  bytesLabel,
  confirmLabel,
  formatCountdown,
  formatDelta,
  impactLines,
  rowsLabel,
} from './confirmation'

const COMMENT_MAX = 500 // websocket.md §4.3
const TICK_MS = 250
const URGENT_S = 30

type Answer = 'approve' | 'reject'

interface Props {
  confirmation: ConfirmationRequired
  connected: boolean
  onAnswer: (decision: Answer, comment?: string) => void
}

// Blocking modal for a destructive action (docs/keynote/ui/ModaleHITL). Mounted once per
// action_id: its mount time is the reception time of confirmation_required.
export function ConfirmationModal({ confirmation, connected, onAnswer }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const rejectRef = useRef<HTMLButtonElement>(null)
  const [receivedAt] = useState(() => Date.now())
  const [now, setNow] = useState(receivedAt)
  const [sent, setSent] = useState<Answer | null>(null)
  const [comment, setComment] = useState('')
  const { impact, sql, tool } = confirmation

  // showModal: focus trapped in the dialog, rest of the page inert, Escape handled below.
  // "Refuser" gets the focus, so Enter never confirms by accident.
  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    rejectRef.current?.focus()
    return () => dialog?.close()
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    return () => window.clearInterval(timer)
  }, [])

  // The countdown starts at reception, not at `ts` (§6.3). The engine decides the timeout:
  // at zero the buttons stop, and the modal closes on confirmation_resolved.
  const remaining = confirmation.expires_in_s - (now - receivedAt) / 1000
  const expired = remaining <= 0
  const disabled = sent !== null || expired || !connected

  const answer = (decision: Answer) => {
    if (disabled) return
    setSent(decision)
    const text = comment.trim()
    onAnswer(decision, text === '' ? undefined : text)
  }

  // Escape means "Refuser" (annotation 7): never close without an answer
  const onCancel = (event: SyntheticEvent<HTMLDialogElement>) => {
    event.preventDefault()
    answer('reject')
  }

  const lines = impactLines(impact)
  const writes = impact.operation !== 'INSERT'
  const file = impact.file ?? null // file_write (FILE-01): a file instead of table rows

  return (
    <dialog
      ref={dialogRef}
      className="hitl"
      aria-labelledby="hitl-title"
      aria-describedby="hitl-summary"
      onCancel={onCancel}
    >
      <header className="hitl-header">
        <h2 id="hitl-title">
          <span className="hitl-alert" aria-hidden="true">
            !
          </span>
          Confirmation requise
        </h2>
        <span
          className={`hitl-timer${remaining <= URGENT_S ? ' urgent' : ''}`}
          role="timer"
          aria-label={`Temps restant : ${formatCountdown(remaining)}`}
        >
          ⏱ {formatCountdown(remaining)}
        </span>
      </header>

      <div className="hitl-body">
        <p id="hitl-summary">
          {file !== null ? (
            <>
              L'agent veut{' '}
              {file.exists ? <strong>remplacer un fichier existant</strong> : 'créer un fichier'} via{' '}
              <code>{tool}</code>.
            </>
          ) : (
            <>
              L'agent veut exécuter{' '}
              {writes ? <strong>une action irréversible</strong> : 'une écriture'} via <code>{tool}</code>.
            </>
          )}
        </p>

        {sql !== null && (
          <section className="hitl-section">
            <h3>Requête</h3>
            <SqlCode sql={sql} />
          </section>
        )}

        {file !== null && <FilePreview file={file} />}

        {file === null && (
          <section className="hitl-section">
            <h3>
              Impact <span className="muted">· simulé puis annulé, rien n'est encore modifié</span>
            </h3>
            {lines.length > 0 ? (
              <ul className="hitl-impact">
                {lines.map((line) => (
                  <li key={line.table}>
                    <code>{line.table}</code>
                    <strong className={line.delta < 0 ? 'minus' : undefined}>
                      {formatDelta(line.delta)}
                    </strong>
                  </li>
                ))}
              </ul>
            ) : impact.rows_affected > 0 ? (
              <p>
                <strong>{rowsLabel(impact.rows_affected)}</strong>{' '}
                {impact.operation === 'UPDATE' ? 'modifiée' : 'concernée'}
                {impact.rows_affected > 1 ? 's' : ''}
              </p>
            ) : (
              <p className="muted">Aucune ligne ne serait modifiée.</p>
            )}
          </section>
        )}

        {impact.blocked.length > 0 && (
          <section className="hitl-section hitl-blocked">
            <h3>{file !== null ? 'Refusé par les permissions' : 'Protégés, non modifiables'}</h3>
            <ul>
              {impact.blocked.map((block, index) => (
                <li key={index}>
                  {block.count !== null && <strong>{rowsLabel(block.count)} · </strong>}
                  {block.reason}
                </li>
              ))}
            </ul>
            {file !== null ? (
              <p>Cette écriture sera refusée : aucun fichier ne sera écrit.</p>
            ) : (
              impact.rows_affected === 0 && (
                <p>La base refusera cette instruction tant que ces lignes sont visées.</p>
              )
            )}
          </section>
        )}

        {impact.sample.length > 0 && (
          <section className="hitl-section">
            <h3>
              Échantillon{' '}
              <span className="muted">
                {/* A memory deletion lists its cascades too: "2 sur 1 ligne" would be wrong */}
                {impact.sample.length < impact.rows_affected
                  ? `· ${impact.sample.length} sur ${rowsLabel(impact.rows_affected)}`
                  : `· ${rowsLabel(impact.sample.length)}`}
              </span>
            </h3>
            <Sample rows={impact.sample} />
          </section>
        )}

        <div className="hitl-comment">
          <label htmlFor="hitl-comment">Commentaire (facultatif, journalisé)</label>
          <textarea
            id="hitl-comment"
            rows={2}
            maxLength={COMMENT_MAX}
            value={comment}
            disabled={disabled}
            onChange={(event) => setComment(event.target.value)}
          />
        </div>
      </div>

      <footer className="hitl-footer">
        <p className="muted" role="status">
          {!connected
            ? 'Connexion perdue : la demande sera refusée automatiquement.'
            : sent !== null
              ? 'Réponse envoyée…'
              : expired
                ? 'Délai dépassé, annulation en cours…'
                : 'Sans réponse avant la fin du délai : refus automatique.'}
        </p>
        <button
          ref={rejectRef}
          type="button"
          className="hitl-reject"
          disabled={disabled}
          onClick={() => answer('reject')}
        >
          Refuser
        </button>
        <button
          type="button"
          className="hitl-approve"
          disabled={disabled}
          onClick={() => answer('approve')}
        >
          {confirmLabel(impact)}
        </button>
      </footer>
    </dialog>
  )
}

// file_write: where, how big, new or replaced, and the start of the content as written
function FilePreview({ file }: { file: FileImpact }) {
  return (
    <>
      <section className="hitl-section">
        <h3>
          Fichier <span className="muted">· rien n'est encore écrit</span>
        </h3>
        <p className="hitl-file">
          <code>{file.path}</code>
          <span className={`hitl-file-tag${file.exists ? ' warn' : ''}`}>
            {file.exists ? 'remplace le fichier existant' : 'nouveau fichier'}
          </span>
          <span className="muted">{bytesLabel(file.bytes)}</span>
        </p>
      </section>
      <section className="hitl-section">
        <h3>Début du contenu</h3>
        <pre className="hitl-excerpt">{file.excerpt}</pre>
      </section>
    </>
  )
}

// impact.sample: up to 5 rows, as objects (any columns)
function Sample({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))]
  const cells: Cell[][] = rows.map((row) => columns.map((column) => toCell(row[column])))
  const formats = columns.map((name, column) => columnFormat(name, cells, column))
  return (
    <div className="hitl-sample">
      <table>
        <thead>
          <tr>
            {columns.map((name, column) => (
              <th key={name} scope="col" className={formats[column].numeric ? 'num' : undefined}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cells.map((row, index) => (
            <tr key={index}>
              {row.map((value, column) => (
                <td key={column} className={formats[column].numeric ? 'num' : undefined}>
                  {formatCell(value, formats[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function toCell(value: unknown): Cell {
  if (value === undefined || value === null) return null
  if (typeof value === 'object') return JSON.stringify(value)
  return value as Cell
}
