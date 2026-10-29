import { codeParts } from './inlineCode'

// A server message with its `commands` shown as code (errors, banners)
export function InlineCode({ text }: { text: string }) {
  return (
    <>
      {codeParts(text).map((part, index) =>
        part.code ? <code key={index}>{part.text}</code> : <span key={index}>{part.text}</span>,
      )}
    </>
  )
}
