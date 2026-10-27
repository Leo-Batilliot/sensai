import { CopyButton } from '../shared/CopyButton'
import { useMenu } from '../shared/useMenu'
import { exportUrl, formatSharedAt } from './share'
import { useShares } from './useShares'

interface Props {
  sessionId: string | null
  hasTurns: boolean
  connected: boolean
  sharesVersion: number
  onShare: () => void
  onUnshare: (token: string) => void
}

// Export and share the conversation (X3). Both show the finished turns only.
export function ConversationActions({
  sessionId,
  hasTurns,
  connected,
  sharesVersion,
  onShare,
  onUnshare,
}: Props) {
  const exportMenu = useMenu()
  const shareMenu = useMenu()
  const { shares, localOnly } = useShares(shareMenu.open, sharesVersion)
  const ready = sessionId !== null && hasTurns

  return (
    <div className="conversation-toolbar">
      <details className="toolbar-menu" {...exportMenu.props}>
        <summary className="btn-small" aria-disabled={!ready}>
          ↓ Exporter
        </summary>
        <div className="toolbar-popover">
          {ready ? (
            <>
              <a className="btn-small" href={exportUrl(sessionId, 'md')} download onClick={exportMenu.close}>
                Markdown (.md)
              </a>
              <a className="btn-small" href={exportUrl(sessionId, 'json')} download onClick={exportMenu.close}>
                JSON (.json)
              </a>
              <p className="muted small">Les échanges terminés, avec les requêtes SQL et les vérifications.</p>
            </>
          ) : (
            <p className="muted small">Rien à exporter : posez d'abord une question.</p>
          )}
        </div>
      </details>

      <details className="toolbar-menu" {...shareMenu.props}>
        <summary className="btn-small">Partager</summary>
        <div className="toolbar-popover share-popover">
          <p>
            <strong>Lien en lecture seule</strong>, figé au moment du partage : la suite de la
            conversation n'y apparaît pas.
          </p>
          <p className="share-warning">Toute personne qui a ce lien voit cette conversation.</p>
          {localOnly && (
            // websocket.md §9: the server listens on 127.0.0.1, no other machine reaches it
            <p className="muted small">
              Ce lien ne s'ouvre que sur cet ordinateur : le serveur n'accepte pas les connexions du
              réseau. Pour l'ouvrir depuis un autre poste, lancez le serveur avec{' '}
              <code>--host 0.0.0.0</code> (toute personne du réseau pourra alors utiliser l'interface).
            </p>
          )}
          <button type="button" className="btn-small" disabled={!connected || !hasTurns} onClick={onShare}>
            Créer un lien
          </button>
          {shares.length > 0 && (
            <ul className="share-list">
              {shares.map((share) => (
                <li key={share.token}>
                  <a href={share.url} target="_blank" rel="noreferrer">
                    <code>{share.url}</code>
                  </a>
                  <span className="muted small">{formatSharedAt(share.created_at)}</span>
                  <span className="share-actions">
                    <CopyButton text={share.url} label="Copier le lien" />
                    <button
                      type="button"
                      className="btn-small share-revoke"
                      disabled={!connected}
                      onClick={() => onUnshare(share.token)}
                    >
                      Révoquer
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>
    </div>
  )
}
