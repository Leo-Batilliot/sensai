# SENSAI — Front web

Interface web de SENSAI (fonctionnalité X1) : React 19, TypeScript, Vite. Le build (`web/dist`) est **committé** et servi par FastAPI : la démo se lance avec une seule commande, sans Node (WEB-14). Le front ne contient aucune logique métier : il affiche les événements du moteur et lui envoie les actions de l'utilisateur ([contrat WebSocket](../docs/contracts/websocket.md)).

Prérequis pour modifier le front : Node.js 22 LTS. Pour seulement le lancer : rien, `web/dist` est dans le dépôt.

## Lancer

**Comme pour la démo** (depuis la racine du dépôt) :

```bash
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
| `statusbar/` | Barre d'état : sélecteur de modèle, serveurs MCP et leur état, jauge de contexte |
| `memory/` | Panneau Mémoire : glossaire, entités, relations, faits ; modifier et supprimer (avec confirmation) |
| `judge/` | Badge du juge et son détail, surlignage des affirmations, cartes de chiffres clés et relances |
| `shared/` | Composants communs (bouton Copier) |
| `test/` | Outils des tests seulement (lecture des fixtures), exclus du build |

## Conventions

- Les variables CSS de `src/index.css` reprennent les noms des tokens des maquettes ([docs/keynote/ui](../docs/keynote/ui/)).
- Textes affichés en français, commentaires dans le code en anglais.
- `package-lock.json` et `dist/` sont commités, `node_modules/` est ignoré.
- **Après une modification de `src/`** : `make web-build` (depuis la racine), puis committer `web/dist` avec le reste. `npm run build` écrit `dist/build.json`, l'empreinte des sources du build (`vite.config.ts`) ; le test `tests/unit/web/test_frontend.py` la recalcule sans Node et échoue en CI si `dist/` est plus ancien que les sources. `make web` le signale aussi au démarrage. Les fichiers de test (`*.test.ts`, `src/test/`) ne comptent pas.
- **Cache** : FastAPI sert `dist/assets/` (noms avec empreinte) avec un cache d'un an, et `index.html` en `no-cache`. Un nouveau build s'affiche donc au rechargement normal de la page, sans Ctrl+Maj+R.
