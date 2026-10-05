# Timeline

Liste chronologique des étapes de l'agent pendant un tour : outil appelé, arguments, durée et statut.

## Annotations
1. Une ligne par `tool_call` (serveur · outil), complétée par `tool_result` (durée, résumé, statut). Le juge apparaît comme dernière étape.

## Règles
- Une erreur d'outil reste visible avec son message ; l'essai corrigé suit en dessous.
- Une étape dépliée montre les arguments JSON (6 lignes max + « copier »).
- La timeline reste consultable après la réponse : chaque message garde un lien vers son tour.
- Repliable en entier ; ouverte par défaut pendant la génération.

## Événements
`tool_call`, `tool_result`, `sql_generated`, `confirmation_required`, `judge_started`, `judge_report`.

## User story
US-X1-03.
