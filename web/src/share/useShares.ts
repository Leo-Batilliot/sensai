import { useEffect, useState } from 'react'
import type { ShareInfo } from '../protocol'

interface Shares {
  shares: ShareInfo[]
  localOnly: boolean // the server only accepts this machine: the link opens nowhere else
}

// GET /api/shares, read when the menu opens and after each share or revocation
export function useShares(open: boolean, version: number): Shares {
  const [shares, setShares] = useState<Shares>({ shares: [], localOnly: false })

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    fetch('/api/shares', { signal: controller.signal })
      .then((response) =>
        response.ok ? (response.json() as Promise<{ shares: ShareInfo[]; local_only: boolean }>) : null,
      )
      .then((body) => {
        if (body !== null) setShares({ shares: body.shares, localOnly: body.local_only })
      })
      .catch(() => {
        // Server restarting or menu closed: the list keeps what it showed
      })
    return () => controller.abort()
  }, [open, version])

  return shares
}
