# SENSAI — Copilote BI local

Un collaborateur pose une question métier en français ; l'agent interroge la base de l'entreprise via des outils MCP, répond avec des chiffres vérifiés contre les données, retient le vocabulaire métier et ne modifie jamais les données sans confirmation explicite. Tout tourne en local avec [Ollama](https://ollama.com).

- Conception : [PROJECT.md](PROJECT.md)
- Planning et répartition : [PLANNING.md](PLANNING.md)

> README v0 : installation développeur uniquement. La version complète arrive avec DOC-05 (12/10).

## Prérequis

Sous Windows, travailler dans **WSL2** (Ubuntu).

- Python 3.10 ou plus (l'équipe est sur 3.12)
- Ollama lancé (`ollama serve`) et un modèle compatible outils : `ollama pull qwen3:8b`
- `uv` (fournit `uvx`, pour `mcp-server-time`)
- Node.js 22 LTS (front, MCP Inspector, serveur filesystem)

## Installation

```bash
make install        # crée .venv et installe les dépendances (dev incluses)
make test           # tests hors Ollama et outils externes, comme la CI
make lint           # ruff check + ruff format --check
make help           # toutes les commandes
```

Sans `make` : `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

## Configuration

Tout est optionnel : sans fichier, les valeurs par défaut de [`config.example.toml`](config.example.toml) s'appliquent.

```bash
cp config.example.toml config.toml   # config.toml n'est pas versionné
```

Priorité : argument CLI (`--model`, `--host`, `--config`) > variable d'environnement `SENSAI_<SECTION>_<CLÉ>` (ex. `SENSAI_OLLAMA_MODEL`) > `config.toml` > valeurs par défaut.

## Contribuer

- Une branche par tâche du planning : `feat/<ID>-<slug>` (ex. `feat/CORE-02-ollama-client`)
- Commits [Conventional Commits](https://www.conventionalcommits.org/fr/) : `feat(mcp): handshake initialize`
- PR vers `main`, une relecture obligatoire, CI verte, squash merge
- Détails : [PROJECT.md §13](PROJECT.md#13-conventions-déquipe)
