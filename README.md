# SENSAI — Copilote BI local

Un collaborateur pose une question métier en français ; l'agent interroge la base de l'entreprise via des outils MCP, répond avec des chiffres vérifiés contre les données, retient le vocabulaire métier et ne modifie jamais les données sans confirmation explicite. Tout tourne en local avec [Ollama](https://ollama.com).

> **README v0** (DOC-05) : installation et lancement. La version finale (fonctionnalités, exemples, architecture, résultats d'évaluation) arrive avec la release du 28/10.

## État actuel (`v0.5.0`, mid-term, 13/10)

| Commande | Ce qui marche aujourd'hui | Ce qui arrive |
|---|---|---|
| `make chat` | Chat en terminal sur le moteur réel, assemblé comme le web : outils MCP (lecture et écriture SQL, date), mémoire, confirmation y/N, verdict du juge (il faut `make seed` et Ollama ; sans base, chat simple) | Auto-correction SQL (CORE-09) |
| `make web` | Interface web sur le moteur réel : outils MCP, mémoire, juge, confirmation dans une modale (il faut `make seed` et Ollama) | Sélecteur de modèle, jauge, état MCP (WEB-10) |
| `make web-mock` | Interface web sur le `MockEngine`, qui rejoue des scénarios enregistrés, sans Ollama ni base | — |
| `make seed` | Base de démonstration `data/business.db`, déterministe | — |
| `make eval` | `sensai eval compare AVANT APRÈS` compare deux rapports | Lancer le golden set sur le moteur réel (EVAL-06) |
| `scripts/chat_fake_tools.py` | Chat avec deux outils de remplacement, sans serveur MCP | Outil de dépannage |

## Installation sur un PC Windows vierge (PowerShell)

La voie la plus simple pour une machine de démo : tout s'installe avec `winget`, sans WSL ni `make`. Compter environ 30 minutes, dont le téléchargement du modèle (5 Go).

### 1. Les outils

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id Ollama.Ollama -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
```

**Fermer puis rouvrir PowerShell** pour que les nouvelles commandes soient trouvées, puis :

```powershell
ollama pull qwen3:8b          # le modèle de l'agent (environ 5 Go)
uvx mcp-server-time --help    # met en cache le serveur MCP de date (aucun téléchargement en démo)
```

### 2. Le projet

```powershell
git clone https://github.com/EpitechPGE3-2026/G-AIA-500-LIL-5-1-sensai-4.git sensai
cd sensai
git checkout v0.5.0           # la version de la mid-term (sinon rester sur main)
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Le dépôt est privé : au premier `git clone`, une fenêtre de connexion GitHub s'ouvre.

### 3. Les données et l'interface web

```powershell
.venv\Scripts\python -m sensai seed     # base de démo, date de référence = aujourd'hui
cd web; npm ci; npm run build; cd ..    # construit web/dist
```

### 4. Vérifier

```powershell
.venv\Scripts\python -m sensai chat     # puis /tools : sql_toolbox et time « en ligne », /exit pour quitter
.venv\Scripts\python -m sensai web      # puis ouvrir http://127.0.0.1:8000
```

- Pendant une réponse, `ollama ps` doit indiquer le **GPU** : sur le seul processeur, le modèle est trop lent pour une démo.
- `.venv\Scripts\python -m pytest -m "not ollama and not external"` lance les tests. Sous Windows, 4 tests MCP de fin de processus échouent (`test_close_*`, `test_unsupported_version_is_refused`, `test_missing_base_stops_the_server`) : c'est connu, ils passent sous Linux.
- Pour la démo mid-term : [`docs/keynote/demo-script-midterm.md`](docs/keynote/demo-script-midterm.md).

## Installation sous Linux (Ubuntu, Arch) ou WSL2

Le `Makefile` fonctionne directement. Il faut Python 3.10 ou plus : la CI teste 3.10 et 3.12.

Sous **WSL2** (Ubuntu), suivre les commandes Ubuntu, et cloner le dépôt **dans le système de fichiers de WSL** (`~/…`), pas sous `/mnt/c` : c'est plus rapide et cela évite de mélanger un `.venv` Windows et un `.venv` Linux.

### 1. Outils système

Ubuntu, Debian ou WSL2 :

```bash
sudo apt update
sudo apt install -y git make curl python3 python3-venv python3-pip
```

Arch :

```bash
sudo pacman -Syu --needed git make curl python
```

Vérifier : `python3 --version` affiche 3.10 ou plus.

### 2. Ollama et le modèle

Ubuntu, Debian ou WSL2 :

```bash
curl -fsSL https://ollama.com/install.sh | sh    # installe aussi le service systemd
```

Arch (choisir le paquet selon la carte graphique) :

```bash
sudo pacman -S --needed ollama        # ou ollama-cuda (NVIDIA), ollama-rocm (AMD)
sudo systemctl enable --now ollama
```

Puis, quelle que soit la distribution :

```bash
ollama pull qwen3:8b           # environ 5 Go : à lancer en avance
ollama show qwen3:8b           # « Capabilities » doit contenir « tools »
```

Sans systemd (certaines installations WSL2), lancer `ollama serve &` avant d'utiliser le projet.

Le modèle doit **gérer les outils** : sans cela, l'agent ne peut pas interroger la base. Le modèle par défaut est celui de `[ollama] model` dans [`config.example.toml`](config.example.toml).

### 3. `uv` (pour `uvx` et le serveur MCP de date)

Ubuntu, Debian ou WSL2 :

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env    # ou rouvrir le terminal
```

Arch :

```bash
sudo pacman -S --needed uv
```

Puis : `uvx mcp-server-time --help`. Le serveur est téléchargé une première fois et mis en cache, pour ne pas attendre au lancement.

### 4. Node.js 22 ou plus (interface web)

Ubuntu, Debian ou WSL2 :

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
```

Arch :

```bash
sudo pacman -S --needed nodejs npm
```

Vérifier : `node --version` affiche v22 ou plus. Node n'est nécessaire que pour construire le front (`make web-build`) et pour MCP Inspector.

### 5. Le projet

```bash
git clone https://github.com/EpitechPGE3-2026/G-AIA-500-LIL-5-1-sensai-4.git sensai   # ou git@github.com:… avec une clé SSH
cd sensai
git checkout v0.5.0 # la version de la mid-term (sinon rester sur main)
make install        # crée .venv et installe les dépendances (dev incluses)
make seed           # base de démo, date de référence = aujourd'hui
make web-build      # construit web/dist
make test           # tests sans Ollama ni outils externes, comme la CI
make help           # toutes les commandes
```

- Sans `make` : `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.
- Si la version de Python de la distribution est trop récente pour une dépendance (Arch suit toujours la dernière version), installer une 3.12 avec uv : `uv python install 3.12`, puis `rm -rf .venv && make install PYTHON="$(uv python find 3.12)"`.

### 6. Vérifier

```bash
make chat           # puis /tools : sql_toolbox et time « en ligne », /exit pour quitter
make web            # puis ouvrir http://127.0.0.1:8000
```

## Configuration

Tout est optionnel : sans fichier, les valeurs par défaut de [`config.example.toml`](config.example.toml) s'appliquent.

```bash
cp config.example.toml config.toml   # config.toml n'est pas versionné
```

Les clés à connaître :

| Clé | Rôle |
|---|---|
| `[ollama] model` | Modèle de l'agent (doit être téléchargé et gérer les outils) |
| `[ollama] host` | Adresse du serveur Ollama (`http://localhost:11434`) |
| `[database] business_path` | Base métier interrogée par l'agent (`data/business.db`) |
| `[mcp.servers.<nom>]` | Serveurs MCP lancés au démarrage : `command` et `args`. Ajouter une table suffit pour brancher un serveur ; `<nom>` préfixe ses outils (`time__get_current_time`) |
| `[web] port` | Port de l'interface web (`8000`) |

Priorité : argument CLI (`--model`, `--host`, `--config`) > variable d'environnement `SENSAI_<SECTION>_<CLÉ>` (ex. `SENSAI_OLLAMA_MODEL`) > `config.toml` > valeurs par défaut. Une clé inconnue est refusée au lancement, ce qui évite qu'une faute de frappe passe inaperçue.

## Lancer

### Base de démonstration

```bash
make seed           # data/business.db, graine 42, date de référence = aujourd'hui
```

La base est régénérée à l'identique pour une même graine et une même date. Pour figer la date : `.venv/bin/python -m sensai seed --today 2026-10-14`.

### Chat en terminal

```bash
make chat
.venv/bin/python -m sensai chat --model llama3.1:8b   # autre modèle, sans toucher à la config
```

| Commande | Effet |
|---|---|
| `/help` | Aide |
| `/models` | Modèles installés dans Ollama (`*` = modèle actuel) |
| `/model <nom>` | Change de modèle, l'historique est conservé |
| `/reset` | Vide l'historique |
| `/tools` | État des serveurs MCP et outils disponibles (« confirmation » si l'outil en demande une) |
| `/memory` | Contenu de la mémoire de l'agent (glossaire, entités, faits) |
| `/exit` ou `Ctrl+D` | Quitte |
| `Ctrl+C` | Pendant une réponse : l'interrompt sans quitter. À l'invite : quitte |

Au démarrage, le chat lance les serveurs MCP de la configuration. Un serveur qui ne démarre pas (par exemple `time` sans `uv`) est signalé, et les autres fonctionnent. Après `make seed` :

```text
> Quels sont nos 5 clients les plus rentables ce trimestre et quel est leur délai moyen de paiement ?
```

La timeline des outils (`• time__get_current_time`, `• sql_toolbox__run_select`, le SQL, le résultat) s'affiche avant la réponse, puis le verdict du juge (« Vérification : Vérifiée »). Le script de la démo mid-term est dans [`docs/keynote/demo-script-midterm.md`](docs/keynote/demo-script-midterm.md).

### Interface web

```bash
make web-build      # npm ci + build du front dans web/dist
make seed           # une fois : la base de démonstration data/business.db
make web            # http://127.0.0.1:8000, sur le moteur réel (Ollama lancé)
make web-mock       # ou : sur le MockEngine, sans Ollama ni base
```

Développement du front avec rechargement à chaud : voir [`web/README.md`](web/README.md).

### Tests et qualité

```bash
make test           # comme la CI : sans Ollama ni serveurs externes
make test-all       # tout, y compris les tests sur un vrai Ollama et sur uvx mcp-server-time
make lint           # ruff check + ruff format --check
make format         # corrige ce qui peut l'être
```

## Dépannage

| Symptôme | Cause et solution |
|---|---|
| « Ollama injoignable sur http://localhost:11434 » | Ollama n'est pas lancé : `ollama serve`, puis vérifier avec `curl http://localhost:11434/api/tags`. La session reste ouverte : reposer la question suffit. |
| Ollama installé côté Windows, projet dans WSL : toujours injoignable | Par défaut, `localhost` dans WSL n'atteint pas Windows. Au choix : installer Ollama dans WSL ; ou activer le réseau en miroir (`[wsl2]` puis `networkingMode=mirrored` dans `%UserProfile%\.wslconfig`, puis `wsl --shutdown`) ; ou lancer Ollama sous Windows avec `OLLAMA_HOST=0.0.0.0` et passer l'IP de Windows avec `--host`. |
| « Le modèle « … » n'est pas installé » au démarrage | Le chat liste les modèles installés et s'arrête. Télécharger celui qui manque (`ollama pull <modèle>`) ou en choisir un autre (`--model`, `[ollama] model`). |
| « Le modèle … ne gère pas les outils » | Le chat fonctionne, mais pas les questions sur les données. Choisir un modèle dont `ollama show` liste la capacité `tools`. |
| `uvx: command not found`, ou « Serveur MCP « time » : la commande `uvx` est introuvable » | `uv` n'est pas installé ou pas dans le `PATH` : refaire l'étape 3, puis `source $HOME/.local/bin/env` ou rouvrir le terminal. Vérifier avec `uvx --version`. |
| Le premier lancement du serveur de date est lent | `uvx` télécharge `mcp-server-time` : le précharger avec `uvx mcp-server-time --help`. |
| Port 8000 occupé (`address already in use`) | Trouver le processus : `ss -ltnp \| grep :8000`. Ou changer de port : `SENSAI_WEB_PORT=8001 make web-mock`. |
| « Interface web absente » au lancement du serveur | Le front n'est pas construit : `make web-build`. |
| `.venv/bin/python: No such file or directory` | Le `.venv` a été créé sous Windows (`.venv\Scripts`). Supprimer `.venv` puis `make install` dans WSL. |
| « Base absente : data/business.db » | `make seed`. |

## Documentation

- [`docs/contracts/`](docs/contracts/) : contrats entre les briques (événements du moteur, outils et confirmation, WebSocket, mémoire, rapport du juge)
- [`docs/user-stories/`](docs/user-stories/) : user stories par fonctionnalité
- [`docs/keynote/scenario.md`](docs/keynote/scenario.md) : scénario de démonstration
- [`web/README.md`](web/README.md) : front web
- [`eval/README.md`](eval/README.md) : golden set et évaluation

## Contribuer

- Une branche par tâche du planning : `feat/<ID>-<slug>` (ex. `feat/CORE-02-ollama-client`), `fix/…`, `docs/…`
- Commits : `[Initiales][chemin] type: ID, résumé` (ex. `[G.WI][src/sensai/core] feat: CORE-06, tool-calling loop`)
- PR vers `main`, une relecture obligatoire, CI verte
- Commentaires du code en anglais, textes affichés en français
