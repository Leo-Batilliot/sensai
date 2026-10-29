import { useEffect, useMemo, useState } from 'react'
import { MessageList } from '../chat/MessageList'
import { NotePanel } from '../note/NotePanel'
import { isBlank } from '../note/note'
import type { SharedSnapshot } from '../protocol'
import { formatSharedAt, sharedState } from './share'

type Loaded = SharedSnapshot | 'missing' | null

// The page of a shared link (X3): the frozen snapshot, read only, without WebSocket or action
export function SharedPage({ token }: { token: string }) {
  const [snapshot, setSnapshot] = useState<Loaded>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`/api/shares/${encodeURIComponent(token)}`, { signal: controller.signal })
      .then((response): Promise<Loaded> | Loaded =>
        response.ok ? (response.json() as Promise<SharedSnapshot>) : 'missing',
      )
      .then((body) => setSnapshot(body))
      .catch(() => {
        if (!controller.signal.aborted) setSnapshot('missing')
      })
    return () => controller.abort()
  }, [token])

  const state = useMemo(
    () => (snapshot !== null && snapshot !== 'missing' ? sharedState(snapshot) : null),
    [snapshot],
  )

  return (
    <div className="shared-page">
      <header className="statusbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            S
          </span>
          <strong>SENSAI</strong>
          <span className="muted">Nordvent Distribution</span>
        </div>
        <div className="statusbar-items">
          <span className="chip">Lecture seule</span>
        </div>
      </header>
      <main className="conversation" aria-label="Conversation partagée">
        {snapshot === null && <p className="shared-status muted">Chargement…</p>}
        {snapshot === 'missing' && (
          <p className="shared-status">Ce lien de partage n'existe pas ou a été révoqué.</p>
        )}
        {snapshot !== null && snapshot !== 'missing' && state !== null && (
          <>
            <p className="shared-info" role="note">
              Conversation partagée le {formatSharedAt(snapshot.created_at)} · modèle {snapshot.model}
              {snapshot.persona !== null && ` · profil ${snapshot.persona.name}`}. Toute personne qui
              a ce lien voit cette conversation.
            </p>
            <MessageList
              turns={state.turns}
              personaMarks={state.personaMarks}
              connected={false}
              refusalKey={0}
              onAnswer={() => undefined}
              canAsk={false}
              onAsk={() => undefined}
            />
            {snapshot.document !== null && !isBlank(snapshot.document) && (
              // The note at its revision of the share (websocket.md §4.10), read only
              <section className="shared-note" aria-label="Note du comité">
                <h2>Note du comité</h2>
                <NotePanel
                  note={snapshot.document}
                  revisions={[]}
                  recent={new Map()}
                  last={null}
                  canAct={false}
                />
              </section>
            )}
          </>
        )}
      </main>
    </div>
  )
}
