import { useEffect, useRef, useState } from 'react'
import type { Turn } from '../session/reducer'
import { confirmationOutcome, type Outcome } from './confirmation'

const TOAST_MS = 6000

// A toast when a confirmation ends while the user watches (docs/keynote/ui/ModaleHITL,
// "Après la décision"). Compared with the previous state of the same turn, so replaying
// the history of a resumed session never shows old toasts.
export function useOutcomeToast(turns: Turn[]): { toast: Outcome | null; dismiss: () => void } {
  const previous = useRef(turns)
  const [toast, setToast] = useState<Outcome | null>(null)

  useEffect(() => {
    const before = previous.current
    previous.current = turns
    const last = turns[turns.length - 1]
    const old = last === undefined ? undefined : before.find((turn) => turn.id === last.id)
    if (last === undefined || old === undefined) return
    const outcome = confirmationOutcome(last)
    if (outcome?.final && outcome.key !== confirmationOutcome(old)?.key) setToast(outcome)
  }, [turns])

  useEffect(() => {
    if (toast === null) return
    const timer = window.setTimeout(() => setToast(null), TOAST_MS)
    return () => window.clearTimeout(timer)
  }, [toast])

  return { toast, dismiss: () => setToast(null) }
}
