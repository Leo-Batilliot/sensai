# SENSAI — Front web

Interface web de SENSAI (fonctionnalité X1) : React 19, TypeScript, Vite. Le build (`web/dist`) est servi par FastAPI, donc la démo se lance avec une seule commande. Le front ne contient aucune logique métier : il affiche les événements du moteur et lui envoie les actions de l'utilisateur ([contrat WebSocket](../docs/contracts/websocket.md)).

Prérequis : Node.js 22 LTS.

## Lancer

**Comme pour la démo** (depuis la racine du dépôt) :

```bash
make web-build   # npm ci + build → web/dist
make web         # http://127.0.0.1:8000
```

**En développement**, avec rechargement à chaud : deux terminaux.

```bash
make web                    # terminal 1 : backend FastAPI sur :8000
cd web && npm run dev       # terminal 2 : front sur http://localhost:5173
```

Le serveur de développement de Vite redirige `/api` et `/ws` vers le backend (`vite.config.ts`) : même origine, pas de CORS à configurer.

## Scripts

| Commande (dans `web/`) | Rôle |
|---|---|
| `npm run dev` | Serveur de développement avec rechargement à chaud |
| `npm run build` | Vérification des types (`tsc -b`) puis build dans `dist/` |
| `npm run lint` | Lint avec oxlint |
| `npm run preview` | Sert le build localement (sans backend) |

## Conventions

- Les variables CSS de `src/index.css` reprennent les noms des tokens des maquettes ([docs/keynote/ui](../docs/keynote/ui/)).
- Textes affichés en français, commentaires dans le code en anglais.
- `package-lock.json` est commité ; `node_modules/` et `dist/` sont ignorés (`dist/` est ajouté de force aux tags de livraison).
