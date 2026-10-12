# SENSAI — Copilote BI local

Un collaborateur pose une question métier en français ; l'agent interroge la base de l'entreprise via des outils MCP, répond avec des chiffres vérifiés contre les données, retient le vocabulaire métier et ne modifie jamais les données sans confirmation explicite. Tout tourne en local avec [Ollama](https://ollama.com).

> **README v0** (DOC-05) : installation et lancement. La version finale (fonctionnalités, exemples, architecture, résultats d'évaluation) arrive avec la release du 28/10.

## État actuel (v0, 12/10)

| Commande | Ce qui marche aujourd'hui | Ce qui arrive |
|---|---|---|
| `make chat` | Chat en terminal : streaming, historique, commandes, gestion des erreurs, choix du modèle | Outils MCP (SQL, date), mémoire, HITL et juge avec l'intégration (CORE-10) |
| `scripts/chat_fake_tools.py` | Chat avec deux outils de remplacement (horloge locale, `SELECT` en lecture seule sur la base de démo) : boucle d'outils et prompt SQL | Remplacé par `make chat` une fois les vrais serveurs MCP branchés |
| `make web-mock` | Interface web sur le `MockEngine`, qui rejoue des scénarios enregistrés | `make web` sur le moteur réel (WEB-08) |
| `make seed` | Base de démonstration `data/business.db`, déterministe | — |
| `make eval` | Pas encore implémenté | Harness d'évaluation (EVAL-06a) |

## Installation

Sous Windows, travailler dans **WSL2** (Ubuntu) : les serveurs MCP en stdio et `uvx` y sont plus simples, et le `Makefile` y fonctionne. Cloner le dépôt **dans le système de fichiers de WSL** (`~/…`), pas sous `/mnt/c` : c'est plus rapide et cela évite de mélanger un `.venv` Windows et un `.venv` Linux.

### 1. Outils système

```bash
sudo apt update
sudo apt install -y git make python3 python3-venv python3-pip
python3 --version   # 3.10 ou plus (l'équipe est sur 3.12)
```

### 2. Ollama et le modèle

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &                 # inutile si Ollama tourne déjà en service
ollama pull qwen3:8b           # plusieurs Go : à lancer en avance
ollama show qwen3:8b           # « Capabilities » doit contenir « tools »
```

Le modèle doit **gérer les outils** : sans cela, l'agent ne peut pas interroger la base. Le modèle par défaut est celui de `[ollama] model` dans [`config.example.toml`](config.example.toml).

### 3. `uv` (pour `uvx` et le serveur MCP de date)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env    # ou rouvrir le terminal
uvx mcp-server-time --help     # télécharge le serveur une première fois, pour ne pas attendre au lancement
```

### 4. Node.js 22 LTS (interface web)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
node --version                 # v22.x
```

Node n'est nécessaire que pour construire le front (`make web-build`) et pour MCP Inspector.

### 5. Le projet

```bash
git clone git@github.com:EpitechPGE3-2026/G-AIA-500-LIL-5-1-sensai-4.git sensai
cd sensai
make install        # crée .venv et installe les dépendances (dev incluses)
make test           # tests sans Ollama ni outils externes, comme la CI
make help           # toutes les commandes
```

Sans `make` : `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

### Variante : Windows sans WSL (PowerShell)

Le `Makefile` ne fonctionne pas sous PowerShell. Installer Ollama pour Windows ([ollama.com/download](https://ollama.com/download)) et Python 3.12, puis :

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -m "not ollama and not external"   # = make test
.venv\Scripts\python -m sensai seed                                 # = make seed
.venv\Scripts\python -m sensai chat                                 # = make chat
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
| `/tools`, `/memory` | Outils et mémoire de l'agent (disponibles après MCP-04 et MEM-03) |
| `/exit` ou `Ctrl+D` | Quitte |
| `Ctrl+C` | Pendant une réponse : l'interrompt sans quitter. À l'invite : quitte |

Pour essayer dès maintenant les questions sur les données (après `make seed`) :

```bash
.venv/bin/python scripts/chat_fake_tools.py
```

```text
> Quels sont nos 5 clients les plus rentables ce trimestre et quel est leur délai moyen de paiement ?
```

La timeline des outils (`• time__get_current_time`, `• sql_toolbox__run_select`, le SQL, le résultat) s'affiche avant la réponse.

### Interface web

```bash
make web-build      # npm ci + build du front dans web/dist
make web-mock       # http://127.0.0.1:8000, sur le MockEngine
```

`make web` (moteur réel) refuse de démarrer tant que WEB-08 n'est pas livré. Développement du front avec rechargement à chaud : voir [`web/README.md`](web/README.md).

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
