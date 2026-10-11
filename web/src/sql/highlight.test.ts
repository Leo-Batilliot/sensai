import { describe, expect, it } from 'vitest'
import { layoutSql, tokenize } from './highlight'

const NOMINAL =
  "WITH margin AS (SELECT o.client_id, SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) AS marge FROM orders o JOIN order_lines ol ON ol.order_id = o.id WHERE o.order_date >= '2026-10-01' GROUP BY o.client_id) SELECT c.name, ROUND(m.marge, 2) AS marge_eur FROM margin m JOIN clients c ON c.id = m.client_id LEFT JOIN delay d ON d.client_id = m.client_id ORDER BY m.marge DESC LIMIT 5"

const text = (tokens: { text: string }[]) => tokens.map((token) => token.text).join('')

describe('tokenize', () => {
  it('gives the input back, character for character', () => {
    for (const sql of [NOMINAL, "SELECT 'l''an', \"a b\" -- note\nFROM t /* x */", 'SELECT é€ FROM [t]']) {
      expect(text(tokenize(sql))).toBe(sql)
    }
  })

  it('classifies keywords, functions, strings and numbers', () => {
    const kinds = Object.fromEntries(
      tokenize("select ROUND(x, 2) FROM t WHERE d >= '2026-10-01'")
        .filter((token) => token.kind !== 'space')
        .map((token) => [token.text, token.kind]),
    )
    expect(kinds).toMatchObject({
      select: 'keyword',
      ROUND: 'function',
      x: 'identifier',
      '2': 'number',
      FROM: 'keyword',
      "'2026-10-01'": 'string',
    })
  })

  it('keeps an unfinished string as a string', () => {
    expect(tokenize("SELECT 'abc").at(-1)).toEqual({ kind: 'string', text: "'abc" })
  })
})

describe('layoutSql', () => {
  it('only changes whitespace', () => {
    expect(text(layoutSql(NOMINAL)).replace(/\s+/g, ' ')).toBe(NOMINAL)
  })

  it('breaks before the top-level clauses, not inside the CTE', () => {
    const lines = text(layoutSql(NOMINAL)).split('\n')
    expect(lines.map((line) => line.split(' ')[0])).toEqual([
      'WITH',
      'SELECT',
      'FROM',
      'JOIN',
      'LEFT',
      'ORDER',
      'LIMIT',
    ])
  })

  it('keeps DELETE FROM and a subquery on one line', () => {
    const sql = "DELETE FROM clients WHERE id NOT IN (SELECT client_id FROM invoices)"
    expect(text(layoutSql(sql)).split('\n')).toEqual([
      'DELETE FROM clients',
      'WHERE id NOT IN (SELECT client_id FROM invoices)',
    ])
  })
})
