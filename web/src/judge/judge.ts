// What the interface says about the judge's verdict (events.md §3.11, judge.md §2).
// The badge and the scores come from the engine: the front only words and places them.

import type { Claim, JudgeReport } from '../protocol'

export type BadgeTone = 'pending' | 'ok' | 'warn' | 'bad' | 'unavailable'

export interface BadgeView {
  tone: BadgeTone
  icon: string // always shown: the colour alone is not enough
  label: string
  hint: string | null
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count > 1 ? 's' : ''}`
}

function count(claims: Claim[], verdict: Claim['verdict']): number {
  return claims.filter((claim) => claim.verdict === verdict).length
}

export function badgeView(judge: 'pending' | JudgeReport): BadgeView {
  if (judge === 'pending') {
    return { tone: 'pending', icon: '◌', label: 'Vérification en cours…', hint: null }
  }
  const claims = judge.report?.claims ?? []
  const { status, unmatched } = judge.numeric_check
  switch (judge.badge) {
    case 'verified':
      return {
        tone: 'ok',
        icon: '✓',
        label: claims.length > 0 ? `Vérifiée · ${claims.length}/${claims.length}` : 'Vérifiée',
        hint: 'Chaque chiffre de la réponse a été retrouvé dans les résultats de la requête.',
      }
    case 'needs_review': {
      const unverifiable = count(claims, 'unverifiable')
      const detail =
        status === 'inconclusive'
          ? 'résultat tronqué'
          : unverifiable > 0
            ? `${plural(unverifiable, 'affirmation')} non vérifiable${unverifiable > 1 ? 's' : ''}`
            : null
      return {
        tone: 'warn',
        icon: '!',
        label: detail === null ? 'À vérifier' : `À vérifier · ${detail}`,
        hint: "Une partie de la réponse n'a pas pu être contrôlée contre les données.",
      }
    }
    case 'contradicted': {
      const contradicted = count(claims, 'contradicted')
      const detail =
        contradicted > 0
          ? plural(contradicted, 'affirmation')
          : `${plural(unmatched.length, 'chiffre')} absent${unmatched.length > 1 ? 's' : ''} des données`
      return {
        tone: 'bad',
        icon: '✗',
        label: `Contredite · ${detail}`,
        hint: 'La réponse contient des chiffres que les données contredisent.',
      }
    }
    case 'unavailable':
      return {
        tone: 'unavailable',
        icon: '⊘',
        label: 'Vérification indisponible',
        hint: "Le juge n'a pas pu vérifier cette réponse : les chiffres n'ont pas été contrôlés.",
      }
  }
}

// --- highlighting in the answer -------------------------------------------------------

export type MarkKind = 'contradicted' | 'unverifiable' | 'supported'

export interface Mark {
  kind: MarkKind
  note: string // shown on hover
}

export interface Highlighted {
  markdown: string
  marks: Mark[]
}

// A highlighted passage becomes a Markdown link to "#sensai-judge-<n>", which the answer
// renders as <mark>: the claims are copied word for word from the Markdown source, so
// they are found in it before rendering, emphasis included.
export const MARK_PREFIX = '#sensai-judge-'

interface Candidate extends Mark {
  text: string
}

interface Range extends Mark {
  start: number
  end: number
}

// Text that can be wrapped in [ ](…) without breaking the Markdown around it
function markable(text: string): boolean {
  if (text.trim() === '' || /[[\]\n]/.test(text)) return false
  const even = (part: string, marker: string) => part.split(marker).length % 2 === 1
  // "**" and "*" counted apart: "**la marge" opens a bold it does not close
  return even(text, '**') && even(text.replaceAll('**', ''), '*') && even(text, '`')
}

function candidates(judge: JudgeReport): Candidate[] {
  const claims = judge.report?.claims ?? []
  const { status, unmatched } = judge.numeric_check
  const claim = (kind: MarkKind) =>
    claims
      .filter((c) => c.verdict === kind)
      .map((c) => ({
        kind,
        text: c.text,
        note:
          kind === 'contradicted'
            ? `Contredite par les données${c.expected_value ? ` · valeur attendue : ${c.expected_value}` : ''} · ${c.evidence}`
            : kind === 'unverifiable'
              ? `Non vérifiable · ${c.evidence}`
              : `Vérifiée · ${c.evidence}`,
      }))
  const numbers = unmatched.map((text) =>
    status === 'inconclusive'
      ? {
          kind: 'unverifiable' as const,
          text,
          note: 'Chiffre absent des lignes reçues : le résultat était tronqué',
        }
      : { kind: 'contradicted' as const, text, note: 'Chiffre absent des résultats de la requête' },
  )
  // The most serious first: a passage keeps the first mark that claims it
  return [...claim('contradicted'), ...numbers, ...claim('unverifiable'), ...claim('supported')]
}

export function highlight(content: string, judge: JudgeReport): Highlighted {
  const ranges: Range[] = []
  for (const { text, kind, note } of candidates(judge)) {
    if (!markable(text)) continue
    let start = content.indexOf(text)
    while (start !== -1) {
      const end = start + text.length
      if (ranges.every((range) => end <= range.start || start >= range.end)) {
        ranges.push({ start, end, kind, note })
        break
      }
      start = content.indexOf(text, start + 1)
    }
  }
  ranges.sort((a, b) => a.start - b.start)
  let markdown = ''
  let cursor = 0
  ranges.forEach((range, index) => {
    markdown += `${content.slice(cursor, range.start)}[${content.slice(range.start, range.end)}](${MARK_PREFIX}${index})`
    cursor = range.end
  })
  markdown += content.slice(cursor)
  return { markdown, marks: ranges.map(({ kind, note }) => ({ kind, note })) }
}

// --- key figures ----------------------------------------------------------------------

const figure = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 })
// An amount in euros keeps its cents: 48 712,40 €, not 48 712,4 €
const euros = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function formatFigure(value: number, unit: string): string {
  const shown = unit === 'EUR' ? '€' : unit.trim()
  const number = shown === '€' && !Number.isInteger(value) ? euros : figure
  return shown === '' ? number.format(value) : `${number.format(value)} ${shown}`
}
