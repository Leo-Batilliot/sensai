import { describe, expect, it } from 'vitest'
import { codeParts } from './inlineCode'

describe('codeParts', () => {
  it('shows the commands between backticks as code', () => {
    expect(codeParts('Ollama injoignable — lancez `ollama serve`.')).toEqual([
      { text: 'Ollama injoignable — lancez ', code: false },
      { text: 'ollama serve', code: true },
      { text: '.', code: false },
    ])
  })

  it('keeps a text without backticks whole', () => {
    expect(codeParts('Connexion perdue.')).toEqual([{ text: 'Connexion perdue.', code: false }])
  })

  it('leaves an unpaired backtick as it is', () => {
    expect(codeParts('lancez `make seed')).toEqual([{ text: 'lancez `make seed', code: false }])
  })
})
