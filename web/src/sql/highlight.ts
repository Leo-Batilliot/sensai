// Small SQL tokenizer for the SQL block: colours and line breaks only. The copied SQL is
// always the original text of sql_generated, never this layout.

export type SqlTokenKind =
  | 'keyword'
  | 'function'
  | 'string'
  | 'number'
  | 'comment'
  | 'identifier'
  | 'punctuation'
  | 'space'

export interface SqlToken {
  kind: SqlTokenKind
  text: string
}

const KEYWORDS = new Set(
  (
    'SELECT FROM WHERE AND OR NOT IN IS NULL AS ON JOIN LEFT RIGHT INNER OUTER FULL CROSS ' +
    'NATURAL GROUP BY ORDER HAVING LIMIT OFFSET WITH RECURSIVE UNION ALL EXCEPT INTERSECT ' +
    'DISTINCT CASE WHEN THEN ELSE END ASC DESC INSERT INTO VALUES UPDATE SET DELETE BETWEEN ' +
    'LIKE GLOB EXISTS CAST USING'
  ).split(' '),
)

// Clauses that start a new line, at the top level of the query only
const CLAUSES = new Set(['SELECT', 'FROM', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'UNION'])
const JOIN_PREFIXES = new Set(['LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS', 'NATURAL'])

// Every character belongs to exactly one token: joining the texts gives the input back
const TOKEN =
  /(\s+)|(--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|('(?:[^']|'')*'?)|("(?:[^"]|"")*"?|`[^`]*`?|\[[^\]]*\]?)|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g

export function tokenize(sql: string): SqlToken[] {
  const tokens: SqlToken[] = []
  for (const match of sql.matchAll(TOKEN)) {
    const [text, space, comment, string, quoted, number, word] = match
    if (space !== undefined) tokens.push({ kind: 'space', text })
    else if (comment !== undefined) tokens.push({ kind: 'comment', text })
    else if (string !== undefined) tokens.push({ kind: 'string', text })
    else if (quoted !== undefined) tokens.push({ kind: 'identifier', text })
    else if (number !== undefined) tokens.push({ kind: 'number', text })
    else if (word !== undefined) {
      tokens.push({ kind: KEYWORDS.has(word.toUpperCase()) ? 'keyword' : 'identifier', text })
    } else tokens.push({ kind: 'punctuation', text })
  }
  // An identifier directly followed by "(" is a function call: SUM(, ROUND(, julianday(
  tokens.forEach((token, index) => {
    if (token.kind === 'identifier' && tokens[index + 1]?.text === '(') token.kind = 'function'
  })
  return tokens
}

// The model writes the whole query on one line: break it before the top-level clauses
export function layoutSql(sql: string): SqlToken[] {
  const tokens = tokenize(sql)
  let depth = 0
  let previousWord = ''
  tokens.forEach((token, index) => {
    if (token.text === '(') depth += 1
    else if (token.text === ')') depth = Math.max(0, depth - 1)
    if (token.kind !== 'keyword') {
      if (token.kind !== 'space') previousWord = ''
      return
    }
    const word = token.text.toUpperCase()
    const before = tokens[index - 1]
    const startsLine =
      depth === 0 &&
      before?.kind === 'space' &&
      index > 1 &&
      ((CLAUSES.has(word) && !(word === 'FROM' && previousWord === 'DELETE')) ||
        (word === 'JOIN' && !JOIN_PREFIXES.has(previousWord)) ||
        (JOIN_PREFIXES.has(word) && !JOIN_PREFIXES.has(previousWord)))
    if (startsLine) before.text = '\n'
    previousWord = word
  })
  return tokens
}
