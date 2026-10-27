// Clarifying question of the agent (X4, websocket.md §4.8, §6.4): what the card shows.
// The engine counts the delay and decides; the front only displays and sends `answer`.

import type { QuestionAnswered, QuestionAsked } from '../protocol'
import type { Turn } from '../session/reducer'

export const ANSWER_MAX = 500 // websocket.md §4.8

// The question waiting for the user in this turn, if any
export function pendingQuestion(turn: Turn): QuestionAsked | null {
  return turn.status === 'awaiting_answer' ? turn.question : null
}

export interface ClosedQuestion {
  answered: boolean
  text: string
}

// What stays in the message once the question is closed, also after a reload
export function closedQuestion(question: QuestionAsked, answer: QuestionAnswered): ClosedQuestion {
  switch (answer.status) {
    case 'answered': {
      const option = question.options.find((candidate) => candidate.id === answer.option_id)
      return { answered: true, text: option?.label ?? answer.text ?? '' }
    }
    case 'timeout':
      return { answered: false, text: "Sans réponse dans le délai : l'agent a fait une hypothèse." }
    default:
      return { answered: false, text: 'Question annulée.' }
  }
}
