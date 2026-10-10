// Questions of the demo scenario (docs/keynote/scenario.md), one click to ask
const EXAMPLES = [
  'Quels sont nos 5 clients les plus rentables ce trimestre ?',
  'Quelle est la marge de Brasserie Lemaire ce trimestre ?',
  "Quel est le chiffre d'affaires HT de ce trimestre ?",
]

interface Props {
  disabled: boolean
  onAsk: (question: string) => void
}

export function EmptyState({ disabled, onAsk }: Props) {
  return (
    <div className="messages">
      <div className="empty-state">
        <h1>Que voulez-vous savoir sur vos données ?</h1>
        <p className="muted">
          Les réponses sont vérifiées contre la base. Rien ne quitte cette machine.
        </p>
        <div className="examples">
          {EXAMPLES.map((question) => (
            <button
              key={question}
              type="button"
              className="example"
              disabled={disabled}
              onClick={() => onAsk(question)}
            >
              {question}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
