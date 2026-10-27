import { useEffect, useRef, useState, type SyntheticEvent } from 'react'

// A <details> menu that also closes on a click outside it and on Escape, like a menu
// should. The native element only closes when its own summary is clicked again.
export function useMenu() {
  const ref = useRef<HTMLDetailsElement>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      ref.current?.querySelector('summary')?.focus() // back where the user was
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return {
    open,
    close: () => setOpen(false),
    // Spread on the <details>: React drives `open`, the summary click still toggles it
    props: {
      ref,
      open,
      onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => setOpen(event.currentTarget.open),
    },
  }
}
