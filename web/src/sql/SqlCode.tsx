import { useMemo } from 'react'
import { layoutSql } from './highlight'

// SQL coloured and broken before its clauses (SQL block, HITL modal)
export function SqlCode({ sql }: { sql: string }) {
  const tokens = useMemo(() => layoutSql(sql), [sql])
  return (
    <pre className="sql-code">
      <code>
        {tokens.map((token, index) =>
          token.kind === 'space' || token.kind === 'punctuation' ? (
            token.text
          ) : (
            <span key={index} className={`sql-${token.kind}`}>
              {token.text}
            </span>
          ),
        )}
      </code>
    </pre>
  )
}
