import { describe, expect, it } from 'vitest'
import {
  MAX_ROWS,
  asRowSet,
  columnFormat,
  formatCell,
  nextSort,
  sortRows,
  type Cell,
} from './table'

// Intl uses narrow no-break spaces between thousands
const plain = (text: string) => text.replace(/\s/g, ' ')

// tool_result.data of the nominal fixture (k-5)
const ROWS: Cell[][] = [
  ['Brasserie Lemaire', 48712.4, 47.3],
  ['Imprimerie Vasseur', 41208.15, 31.0],
  ['Cabinet Roussel', 37950.0, 28.4],
  ['Garage Delmas', 29880.6, null],
  ['Atelier Morel', 26011.0, 52.9],
]

describe('asRowSet', () => {
  it('reads the data of run_select', () => {
    const data = { columns: ['name', 'marge_eur'], rows: [['A', 1]], row_count: 1, truncated: false }
    expect(asRowSet(data)).toEqual({ columns: ['name', 'marge_eur'], rows: [['A', 1]], truncated: false })
  })

  it('ignores results that are not a table', () => {
    expect(asRowSet(null)).toBeNull()
    expect(asRowSet({ rows_affected: 27, per_table: { clients: -27 } })).toBeNull()
    expect(asRowSet({ op: 'create', kind: 'glossary', ref: 'client rentable' })).toBeNull()
    expect(asRowSet({ columns: 'name', rows: [] })).toBeNull()
  })

  it('never keeps more than row_limit rows', () => {
    const rows = Array.from({ length: MAX_ROWS + 5 }, (_, index) => [index])
    const rowSet = asRowSet({ columns: ['n'], rows, row_count: rows.length, truncated: false })
    expect(rowSet?.rows).toHaveLength(MAX_ROWS)
    expect(rowSet?.truncated).toBe(true)
  })
})

describe('sortRows', () => {
  it('sorts numbers as numbers', () => {
    const rows: Cell[][] = [[9], [10], [100], [2]]
    expect(sortRows(rows, { column: 0, direction: 'asc' }).flat()).toEqual([2, 9, 10, 100])
    expect(sortRows(rows, { column: 0, direction: 'desc' }).flat()).toEqual([100, 10, 9, 2])
  })

  it('sorts text in French order', () => {
    const rows: Cell[][] = [['Zoé'], ['étude'], ['Atelier'], ['eau']]
    expect(sortRows(rows, { column: 0, direction: 'asc' }).flat()).toEqual(['Atelier', 'eau', 'étude', 'Zoé'])
  })

  it('keeps empty values at the bottom in both directions', () => {
    for (const direction of ['asc', 'desc'] as const) {
      const sorted = sortRows(ROWS, { column: 2, direction })
      expect(sorted.at(-1)?.[0]).toBe('Garage Delmas')
    }
  })

  it('returns the server order without a sort, and never changes its input', () => {
    const copy = ROWS.map((row) => row.slice())
    expect(sortRows(ROWS, null)).toBe(ROWS)
    sortRows(ROWS, { column: 1, direction: 'asc' })
    expect(ROWS).toEqual(copy)
  })

  it('cycles ascending, descending, then none', () => {
    const first = nextSort(null, 1)
    expect(first).toEqual({ column: 1, direction: 'asc' })
    const second = nextSort(first, 1)
    expect(second).toEqual({ column: 1, direction: 'desc' })
    expect(nextSort(second, 1)).toBeNull()
    expect(nextSort(second, 0)).toEqual({ column: 0, direction: 'asc' })
  })
})

describe('formatCell', () => {
  it('formats in French with the same decimals on every row', () => {
    const marge = columnFormat('marge_eur', ROWS, 1)
    const delay = columnFormat('delai_moyen_jours', ROWS, 2)
    expect(marge).toEqual({ numeric: true, decimals: 2, grouping: true })
    expect(plain(formatCell(48712.4, marge))).toBe('48 712,40')
    expect(plain(formatCell(37950, marge))).toBe('37 950,00')
    expect(formatCell(31, delay)).toBe('31,0')
    expect(formatCell(null, delay)).toBe('—')
  })

  it('leaves text, years and identifiers alone', () => {
    expect(columnFormat('name', ROWS, 0).numeric).toBe(false)
    expect(formatCell('Brasserie Lemaire', columnFormat('name', ROWS, 0))).toBe('Brasserie Lemaire')
    expect(formatCell(2026, columnFormat('annee', [[2026]], 0))).toBe('2026')
    expect(formatCell(1234, columnFormat('client_id', [[1234]], 0))).toBe('1234')
  })

  it('treats a column with text and numbers as text', () => {
    expect(columnFormat('mixed', [[1], ['a'], [null]], 0).numeric).toBe(false)
  })
})
