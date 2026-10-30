export interface TextPart {
  text: string
  code: boolean
}

// The server writes commands between backticks, as in the CLI (« lancez `ollama serve` »):
// split the text into plain and code parts. An unpaired backtick stays as it is.
export function codeParts(text: string): TextPart[] {
  const pieces = text.split('`')
  if (pieces.length % 2 === 0) return [{ text, code: false }] // odd number of backticks
  return pieces
    .map((piece, index) => ({ text: piece, code: index % 2 === 1 }))
    .filter((part) => part.text !== '')
}
