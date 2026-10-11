import { useEffect, useState } from 'react'

const FEEDBACK_MS = 1500

interface Props {
  text: string
  label?: string
}

export function CopyButton({ text, label = 'Copier' }: Props) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), FEEDBACK_MS)
    return () => window.clearTimeout(timer)
  }, [state])

  const copy = () => {
    copyText(text).then(
      () => setState('copied'),
      () => setState('failed'),
    )
  }

  return (
    <button type="button" className="btn-small" onClick={copy}>
      <span aria-live="polite">
        {state === 'copied' ? 'Copié' : state === 'failed' ? 'Copie impossible' : `⧉ ${label}`}
      </span>
    </button>
  )
}

async function copyText(text: string): Promise<void> {
  // The Clipboard API needs a secure context: not there when opened over plain http on the network
  if (window.isSecureContext && navigator.clipboard !== undefined) {
    return navigator.clipboard.writeText(text)
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.append(area)
  area.select()
  const copied = document.execCommand('copy')
  area.remove()
  if (!copied) throw new Error('copy refused')
}
