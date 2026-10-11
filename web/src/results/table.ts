// Results of run_select: tool_result.data = {columns, rows, row_count, truncated}
// (websocket.md §5.1, P-3). Sorting and formatting happen here, never on the server.

export type Cell = string | number | boolean | null

export interface RowSet {
  columns: string[]
  rows: Cell[][] // row_count is their number, never the total of the query
  truncated: boolean
}

export type SortDirection = 'asc' | 'desc'

export interface Sort {
  column: number
  direction: SortDirection
}

export const MAX_ROWS = 200 // [database] row_limit: the server never sends more

// Null when the result is not a table (memory tool, write, error…)
export function asRowSet(data: Record<string, unknown> | null): RowSet | null {
  if (data === null) return null
  const { columns, rows, truncated } = data
  if (!Array.isArray(columns) || !columns.every((column) => typeof column === 'string')) return null
  if (!Array.isArray(rows) || !rows.every((row) => Array.isArray(row))) return null
  return {
    columns,
    rows: (rows as Cell[][]).slice(0, MAX_ROWS),
    truncated: truncated === true || rows.length > MAX_ROWS,
  }
}

// A column is numeric when all its non-empty values are numbers
export function isNumericColumn(rows: Cell[][], column: number): boolean {
  let numbers = 0
  for (const row of rows) {
    const value = row[column]
    if (value === null || value === undefined) continue
    if (typeof value !== 'number') return false
    numbers += 1
  }
  return numbers > 0
}

const collator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' })

function compareCells(a: Cell, b: Cell): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'number') return -1 // numbers before text in a mixed column
  if (typeof b === 'number') return 1
  return collator.compare(String(a), String(b))
}

// Stable sort; empty values (null) stay at the bottom in both directions
export function sortRows(rows: Cell[][], sort: Sort | null): Cell[][] {
  if (sort === null) return rows
  const sign = sort.direction === 'asc' ? 1 : -1
  return rows.slice().sort((left, right) => {
    const a = left[sort.column] ?? null
    const b = right[sort.column] ?? null
    if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1
    return sign * compareCells(a, b)
  })
}

// Click on a header: ascending, then descending, then back to the server order
export function nextSort(current: Sort | null, column: number): Sort | null {
  if (current === null || current.column !== column) return { column, direction: 'asc' }
  return current.direction === 'asc' ? { column, direction: 'desc' } : null
}

function decimals(value: number): number {
  if (Number.isInteger(value) || !Number.isFinite(value)) return 0
  const text = String(value)
  if (text.includes('e')) return 6
  return Math.min(6, text.length - text.indexOf('.') - 1)
}

export interface ColumnFormat {
  numeric: boolean
  decimals: number // same number of decimals on every row: 31,0 under 47,3
  grouping: boolean // no "2 026" for a year or an identifier
}

export function columnFormat(name: string, rows: Cell[][], column: number): ColumnFormat {
  const numeric = isNumericColumn(rows, column)
  let places = 0
  if (numeric) {
    for (const row of rows) {
      const value = row[column]
      if (typeof value === 'number') places = Math.max(places, decimals(value))
    }
  }
  return { numeric, decimals: places, grouping: !/(^|_)(id|annee|year)$/i.test(name) }
}

const formatters = new Map<string, Intl.NumberFormat>()

export function formatCell(value: Cell, format: ColumnFormat): string {
  if (value === null) return '—'
  if (typeof value !== 'number') return String(value)
  const key = `${format.decimals}/${format.grouping}`
  let formatter = formatters.get(key)
  if (formatter === undefined) {
    formatter = new Intl.NumberFormat('fr-FR', {
      minimumFractionDigits: format.decimals,
      maximumFractionDigits: format.decimals,
      useGrouping: format.grouping,
    })
    formatters.set(key, formatter)
  }
  return formatter.format(value)
}
