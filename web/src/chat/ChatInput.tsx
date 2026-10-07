import { useState, type KeyboardEvent } from 'react'

interface Props {
  busy: boolean
  connected: boolean
  onSend: (content: string) => void
  onCancel: () => void
}

export function ChatInput({ busy, connected, onSend, onCancel }: Props) {
  const [text, setText] = useState('')
  const canSend = connected && !busy && text.trim() !== ''

  const submit = () => {
    if (!canSend) return
    onSend(text)
    setText('')
  }

  // Enter sends, Shift+Enter adds a line; never send while an input method is composing
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <textarea
        id="question"
        aria-label="Question"
        rows={1}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={connected ? 'Posez une question sur vos données…' : 'Connexion au serveur…'}
      />
      {busy ? (
        <button type="button" className="stop" onClick={onCancel}>
          Arrêter
        </button>
      ) : (
        <button type="submit" disabled={!canSend}>
          Envoyer
        </button>
      )}
    </form>
  )
}
