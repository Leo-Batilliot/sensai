# Réponses Ollama de test

Fixtures du client Ollama (`tests/unit/test_ollama_client.py`). Les tests les rejouent
avec un faux transport HTTP : la CI n'a pas besoin d'Ollama.

**Origine** : écrites à la main d'après la documentation de l'API Ollama, en attendant un enregistrement réel (Ollama n'était pas encore installé). À remplacer dès que possible avec le script ci-dessous.

| Fichier | Requête |
|---|---|
| `chat_simple.ndjson` | `POST /api/chat` en streaming, une phrase de présentation |
| `chat_tool_call.ndjson` | Même chose avec l'outil `get_current_time` déclaré |
| `chat_thinking.ndjson` | Streaming avec `think: true` |
| `chat_format.json` | `stream: false` avec `format` = un schéma JSON |
| `tags.json` | `GET /api/tags` |
| `show.json` | `POST /api/show` |
| `error_404.json` | Corps de la réponse 404 pour un modèle absent |

Pour les réenregistrer (Ollama lancé, modèle installé) :

```bash
python scripts/record_ollama.py --model qwen3:8b
```

Les tests calculent leurs valeurs attendues à partir de ces fichiers : ils restent
valables après un nouvel enregistrement.
