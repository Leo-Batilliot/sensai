# SENSAI — Front web

Interface web de SENSAI (fonctionnalité X1) : React 19, TypeScript, Vite. Le build (`web/dist`) est servi par FastAPI, donc la démo se lance avec une seule commande. Le front ne contient aucune logique métier : il affiche les événements du moteur et lui envoie les actions de l'utilisateur ([contrat WebSocket](../docs/contracts/websocket.md)).

Prérequis : Node.js 22 LTS.

## Lancer

**Comme pour la démo** (depuis la racine du dépôt) :

```bash
make web-build   # npm ci + build → web/dist
make web         # http://127.0.0.1:8000, sur le moteur réel (make seed et Ollama d'abord)
make web-mock    # ou : sur le MockEngine, qui rejoue les scénarios enregistrés
```

**En développement**, avec rechargement à chaud : deux terminaux.

```bash
make web-mock               # terminal 1 : backend FastAPI sur :8000 (ou make web)
cd web && npm run dev       # terminal 2 : front sur http://localhost:5173
```

Le serveur de développement de Vite redirige `/api` et `/ws` vers le backend (`vite.config.ts`) : même origine, pas de CORS à configurer.

> **Moteur** : `make web` branche le vrai moteur (Ollama, serveurs MCP, mémoire, juge). `make web-mock` (`sensai web --mock`) le remplace par le `MockEngine`, qui rejoue les scénarios de [`tests/fixtures/events/`](../tests/fixtures/events/README.md) : pratique pour travailler le front sans Ollama, avec des réponses identiques à chaque fois.

## Scripts

| Commande (dans `web/`) | Rôle |
|---|---|
| `npm run dev` | Serveur de développement avec rechargement à chaud |
| `npm run build` | Vérification des types (`tsc -b`) puis build dans `dist/` |
| `npm run lint` | Lint avec oxlint |
| `npm test` | Tests Vitest (le reducer rejoue les fixtures de `tests/fixtures/events/`) |
| `npm run preview` | Sert le build localement (sans backend) |

## Structure de `src/`

| Dossier | Contenu |
|---|---|
| `session/` | Connexion WebSocket (`useSession`), état de la conversation (`reducer`), statut des appels d'outils (`steps`) |
| `chat/` | Fil de conversation : messages, saisie, état vide |
| `sql/` | Bloc SQL sous la réponse : coloration, copier, lien vers le tableau |
| `results/` | Tableau de résultats de `run_select` : tri et format français |
| `timeline/` | Étapes de l'agent dans le panneau de droite |
| `hitl/` | Modale de confirmation d'une action destructive, notification après la décision |
| `judge/` | Badge du juge et son détail, surlignage des affirmations, cartes de chiffres clés et relances |
| `shared/` | Composants communs (bouton Copier) |
| `test/` | Outils des tests seulement (lecture des fixtures), exclus du build |

## Conventions

- Les variables CSS de `src/index.css` reprennent les noms des tokens des maquettes ([docs/keynote/ui](../docs/keynote/ui/)).
- Textes affichés en français, commentaires dans le code en anglais.
- `package-lock.json` est commité ; `node_modules/` et `dist/` sont ignorés (`dist/` est ajouté de force aux tags de livraison).
