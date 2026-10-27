import { describe, expect, it } from 'vitest'
import type { EngineEvent, QuestionAnswered } from '../protocol'
import { chatReducer, initialState, isBusy } from '../session/reducer'
import { stepStatus } from '../session/steps'
import { fixture, turnsOf, until } from '../test/fixtures'
import { closedQuestion, pendingQuestion } from './question'

const events = fixture('question.jsonl') // act 1 with its clarifying question (X4)
const asked = until(events, (event) => event.type === 'question_asked')
const isAnswered = (event: EngineEvent) => event.type === 'question_answered'

describe('clarifying question (X4, WEB-16)', () => {
  it('pauses the turn on question_asked: the card opens, the session is busy', () => {
    const state = chatReducer(initialState, { type: 'server', messages: asked })
    const [turn] = state.turns
    expect(turn.status).toBe('awaiting_answer')
    expect(pendingQuestion(turn)?.options.map((option) => option.label)).toEqual([
      'Ce trimestre',
      "Tout l'historique",
    ])
    expect(isBusy(state)).toBe(true) // the chat input waits: the answer goes in the card
    const ask = turn.steps.find((step) => step.tool === 'ask_user')
    expect(ask && stepStatus(turn, ask)).toBe('question')
  })

  it('closes on question_answered and keeps the choice in the message', () => {
    const [turn] = turnsOf(until(events, isAnswered))
    expect(turn.status).toBe('running')
    expect(pendingQuestion(turn)).toBeNull()
    expect(turn.question && turn.answer && closedQuestion(turn.question, turn.answer)).toEqual({
      answered: true,
      text: "Tout l'historique",
    })
  })

  it('the whole turn ends normally, with the answer of the tool in the timeline', () => {
    const [turn] = turnsOf(events)
    expect(turn.status).toBe('done')
    expect(turn.steps[0].result?.summary).toBe("Réponse : Tout l'historique")
  })

  it('says what happened without an answer', () => {
    const [turn] = turnsOf(asked)
    const question = turn.question!
    const closed = (patch: Partial<QuestionAnswered>) =>
      closedQuestion(question, {
        type: 'question_answered',
        session_id: 's',
        turn_id: turn.id,
        ts: 0,
        question_id: question.question_id,
        status: 'answered',
        option_id: null,
        text: null,
        ...patch,
      })
    expect(closed({ text: 'Les 12 derniers mois' })).toEqual({ answered: true, text: 'Les 12 derniers mois' })
    expect(closed({ status: 'timeout' }).answered).toBe(false)
    expect(closed({ status: 'timeout' }).text).toContain('hypothèse')
    expect(closed({ status: 'cancelled' }).text).toBe('Question annulée.')
  })
})
