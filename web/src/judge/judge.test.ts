import { describe, expect, it } from 'vitest'
import type { JudgeReport } from '../protocol'
import { fixture, turnsOf } from '../test/fixtures'
import { MARK_PREFIX, badgeView, formatFigure, highlight } from './judge'

const plain = (text: string) => text.replace(/\s/g, ' ')

function judged(name: string): { content: string; judge: JudgeReport } {
  const turn = turnsOf(fixture(name)).find((t) => t.judge !== null && t.judge !== 'pending')
  if (turn === undefined || turn.judge === null || turn.judge === 'pending') throw new Error(name)
  return { content: turn.content, judge: turn.judge }
}

function report(patch: Partial<JudgeReport>): JudgeReport {
  return {
    type: 'judge_report',
    session_id: 's',
    turn_id: 't',
    ts: 0,
    badge: 'verified',
    scores: { faithfulness: 1 },
    numeric_check: { status: 'ok', unmatched: [] },
    report: null,
    duration_ms: 1000,
    ...patch,
  }
}

const claim = (text: string, verdict: 'supported' | 'contradicted' | 'unverifiable' = 'supported') => ({
  text,
  verdict,
  evidence: 'preuve',
  expected_value: null,
})

const body = (claims: ReturnType<typeof claim>[]) => ({
  claims,
  relevance: 5,
  sql_answers_question: true,
  key_figures: [],
  assumptions: [],
  followups: [],
})

describe('badgeView', () => {
  it('words the badge of each fixture, symbol included', () => {
    expect(badgeView('pending')).toMatchObject({ icon: '◌', label: 'Vérification en cours…' })
    expect(badgeView(judged('nominal.jsonl').judge)).toMatchObject({ icon: '✓', label: 'Vérifiée · 5/5' })
    expect(badgeView(judged('judge_contradiction.jsonl').judge)).toMatchObject({
      icon: '✗',
      label: 'Contredite · 1 affirmation',
    })
  })

  it('counts the missing numbers when no claim is contradicted', () => {
    const view = badgeView(
      report({ badge: 'contradicted', numeric_check: { status: 'mismatch', unmatched: ['12 €', '3 %'] } }),
    )
    expect(view.label).toBe('Contredite · 2 chiffres absents des données')
  })

  it('says why an answer needs a review', () => {
    expect(
      badgeView(report({ badge: 'needs_review', numeric_check: { status: 'inconclusive', unmatched: ['5'] } }))
        .label,
    ).toBe('À vérifier · résultat tronqué')
    expect(
      badgeView(report({ badge: 'needs_review', report: body([claim('a', 'unverifiable')]) })).label,
    ).toBe('À vérifier · 1 affirmation non vérifiable')
  })

  it('says that the figures were not checked when the judge failed', () => {
    const view = badgeView(report({ badge: 'unavailable', scores: { faithfulness: null } }))
    expect(view).toMatchObject({ icon: '⊘', label: 'Vérification indisponible' })
    expect(view.hint).toContain("les chiffres n'ont pas été contrôlés")
  })
})

describe('highlight', () => {
  it('marks the contradicted claim of the contradiction fixture', () => {
    const { content, judge } = judged('judge_contradiction.jsonl')
    const { markdown, marks } = highlight(content, judge)
    expect(markdown).toContain(`[Brasserie Lemaire a dégagé 52 300 € de marge brute](${MARK_PREFIX}0)`)
    expect(marks[0].kind).toBe('contradicted')
    expect(marks[0].note).toContain('valeur attendue : 48 712,40 €')
    // "52 300 €" is inside the contradicted claim: marked once, not twice
    expect(marks.filter((mark) => mark.kind === 'contradicted')).toHaveLength(1)
    expect(marks.map((mark) => mark.kind)).toEqual(['contradicted', 'unverifiable'])
  })

  it('marks claims written in Markdown, emphasis included', () => {
    const { content, judge } = judged('nominal.jsonl')
    const { markdown, marks } = highlight(content, judge)
    expect(marks).toHaveLength(5)
    expect(markdown).toContain(`[**Brasserie Lemaire** : 48 712,40 € de marge`)
    // Only brackets were added: removing the links gives the answer back
    expect(markdown.replace(/\[|\]\(#sensai-judge-\d+\)/g, '')).toBe(content)
  })

  it('marks a missing number on its own, as contradicted or to review', () => {
    const content = 'La marge est de 52 300 €.'
    const mismatch = highlight(content, report({ numeric_check: { status: 'mismatch', unmatched: ['52 300 €'] } }))
    expect(mismatch.markdown).toBe(`La marge est de [52 300 €](${MARK_PREFIX}0).`)
    expect(mismatch.marks[0].kind).toBe('contradicted')
    const truncated = highlight(
      content,
      report({ numeric_check: { status: 'inconclusive', unmatched: ['52 300 €'] } }),
    )
    expect(truncated.marks[0].kind).toBe('unverifiable')
  })

  it('leaves alone what it cannot wrap safely', () => {
    const content = 'Voir **la marge** et [le lien](https://x) puis\nune ligne.'
    const unsafe = report({
      report: body([claim('**la marge'), claim('[le lien]'), claim('puis\nune'), claim('absent')]),
    })
    expect(highlight(content, unsafe)).toEqual({ markdown: content, marks: [] })
  })
})

describe('formatFigure', () => {
  it('formats in French with the unit', () => {
    expect(plain(formatFigure(183762.15, '€'))).toBe('183 762,15 €')
    expect(formatFigure(47.3, 'jours')).toBe('47,3 jours')
    expect(plain(formatFigure(48712.4, 'EUR'))).toBe('48 712,40 €')
    expect(plain(formatFigure(1200, '€'))).toBe('1 200 €')
    expect(formatFigure(3, '')).toBe('3')
  })
})
