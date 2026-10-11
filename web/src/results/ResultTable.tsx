import { useMemo, useState } from 'react'
import { columnFormat, formatCell, nextSort, sortRows, type RowSet, type Sort } from './table'

const count = new Intl.NumberFormat('fr-FR')

function plural(value: number, word: string): string {
  return `${count.format(value)} ${word}${value > 1 ? 's' : ''}`
}

interface Props {
  id: string
  rowSet: RowSet
}

// Rows of run_select (docs/keynote/ui/TableauResultats), sorted in the browser
export function ResultTable({ id, rowSet }: Props) {
  const { columns, rows, truncated } = rowSet
  const [sort, setSort] = useState<Sort | null>(null)
  const formats = useMemo(
    () => columns.map((name, column) => columnFormat(name, rows, column)),
    [columns, rows],
  )
  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort])

  const sortLabel =
    sort === null ? '' : ` · trié par ${columns[sort.column]} ${sort.direction === 'asc' ? '▲' : '▼'}`

  return (
    <section id={id} className="results" tabIndex={-1} aria-label="Résultats de la requête">
      <p className="results-header">
        <strong>Résultats</strong>{' '}
        <span className="muted">
          · {plural(rows.length, 'ligne')} · {plural(columns.length, 'colonne')}
          {sortLabel}
        </span>
      </p>
      {rows.length === 0 ? (
        <p className="results-note muted">La requête n'a renvoyé aucune ligne.</p>
      ) : (
        <div className="results-scroll">
          <table>
            <thead>
              <tr>
                {columns.map((name, column) => {
                  const active = sort?.column === column
                  const direction = active ? sort.direction : null
                  return (
                    <th
                      key={column}
                      scope="col"
                      className={formats[column].numeric ? 'num' : undefined}
                      aria-sort={
                        direction === null ? 'none' : direction === 'asc' ? 'ascending' : 'descending'
                      }
                    >
                      <button type="button" onClick={() => setSort(nextSort(sort, column))}>
                        {name}
                        <span className="sort-mark" aria-hidden="true">
                          {direction === 'asc' ? '▲' : direction === 'desc' ? '▼' : '⇅'}
                        </span>
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, index) => (
                <tr key={index}>
                  {columns.map((_, column) => {
                    const value = row[column] ?? null
                    return (
                      <td
                        key={column}
                        className={`${formats[column].numeric ? 'num' : ''}${value === null ? ' empty' : ''}`}
                      >
                        {formatCell(value, formats[column])}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {truncated && (
        // Never a total: the server stopped reading after row_limit rows (P-3)
        <p className="results-note muted">
          {plural(rows.length, 'ligne')} affichée{rows.length > 1 ? 's' : ''} · résultat tronqué
        </p>
      )}
    </section>
  )
}
