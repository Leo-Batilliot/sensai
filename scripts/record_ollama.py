"""Record real Ollama responses as fixtures for the Ollama client tests.

Usage (Ollama running, model pulled):

    python scripts/record_ollama.py [--host http://localhost:11434] [--model qwen3:8b]

Streams (NDJSON) are written line by line as received; JSON responses are
pretty-printed. Everything goes to tests/fixtures/ollama/. The script then checks that
each recording shows what its test expects (a tool call, some thinking, ...).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ollama"
MISSING_MODEL = "sensai-missing-model"

TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Donne la date et l'heure actuelles dans un fuseau horaire IANA.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Fuseau IANA, ex. Europe/Paris"}
            },
            "required": ["timezone"],
        },
    },
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
}

# Fixed sampling so that two recordings stay as close as possible
OPTIONS = {"temperature": 0, "seed": 42}


def user(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def record_stream(http: httpx.Client, name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    with http.stream("POST", "/api/chat", json=payload) as response:
        response.raise_for_status()
        lines = [line for line in response.iter_lines() if line.strip()]
    (OUT_DIR / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [json.loads(line) for line in lines]


def record_json(
    http: httpx.Client,
    name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    response = http.request(method, path, json=payload)
    if response.status_code != expected_status:
        sys.exit(
            f"{name}: HTTP {response.status_code} instead of {expected_status}: {response.text}"
        )
    data = response.json()
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return data


def joined(lines: list[dict[str, Any]], field: str) -> str:
    return "".join(line.get("message", {}).get(field) or "" for line in lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3:8b")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with httpx.Client(base_url=args.host, timeout=300) as http:
        try:
            version = http.get("/api/version").json().get("version", "?")
        except httpx.ConnectError:
            sys.exit(f"Ollama injoignable sur {args.host} — lancez `ollama serve`.")
        model = args.model

        simple = record_stream(
            http,
            "chat_simple.ndjson",
            {
                "model": model,
                "messages": user("Bonjour, présente-toi en une phrase."),
                "think": False,
                "options": OPTIONS,
            },
        )
        if not (simple[-1].get("done") and joined(simple, "content")):
            problems.append("chat_simple: no text or no final chunk")

        tool = record_stream(
            http,
            "chat_tool_call.ndjson",
            {
                "model": model,
                "messages": user("Quelle heure est-il à Paris ? Utilise l'outil."),
                "tools": [TIME_TOOL],
                "think": False,
                "options": OPTIONS,
            },
        )
        if not any(line.get("message", {}).get("tool_calls") for line in tool):
            problems.append("chat_tool_call: the model did not call the tool")

        thinking = record_stream(
            http,
            "chat_thinking.ndjson",
            {
                "model": model,
                "messages": user("Combien font 17 × 3 ? Réponds en une phrase."),
                "think": True,
                "options": OPTIONS,
            },
        )
        if not joined(thinking, "thinking"):
            problems.append("chat_thinking: no thinking field (check the `think` behaviour)")

        structured = record_json(
            http,
            "chat_format.json",
            "POST",
            "/api/chat",
            {
                "model": model,
                "messages": user("Combien font 2 + 2 ? Réponds en JSON avec la clé answer."),
                "stream": False,
                "format": ANSWER_SCHEMA,
                "think": False,
                "options": OPTIONS,
            },
        )
        try:
            json.loads(structured["message"]["content"])["answer"]
        except (KeyError, TypeError, ValueError):
            problems.append("chat_format: content is not JSON with an `answer` key")

        record_json(http, "tags.json", "GET", "/api/tags")
        show = record_json(http, "show.json", "POST", "/api/show", {"model": model})
        if "tools" not in show.get("capabilities", []):
            problems.append(f"show: {model} does not support tools")

        record_json(
            http,
            "error_404.json",
            "POST",
            "/api/chat",
            {"model": MISSING_MODEL, "messages": user("x"), "stream": False},
            expected_status=404,
        )

    (OUT_DIR / "README.md").write_text(
        README.format(
            origin=f"enregistrées le {date.today():%d/%m/%Y} avec Ollama {version}, "
            f"modèle `{model}`"
        ),
        encoding="utf-8",
    )

    for problem in problems:
        print(f"WARNING {problem}")
    print(f"Fixtures written to {OUT_DIR} (Ollama {version}, {model})")
    return 1 if problems else 0


README = """\
# Réponses Ollama de test

Fixtures du client Ollama (`tests/unit/test_ollama_client.py`). Les tests les rejouent
avec un faux transport HTTP : la CI n'a pas besoin d'Ollama.

**Origine** : {origin}.

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
"""


if __name__ == "__main__":
    sys.exit(main())
