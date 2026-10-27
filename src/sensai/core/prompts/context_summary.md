Tu résumes le début d'une conversation entre un utilisateur et l'assistant d'analyse de Nordvent Distribution, pour que l'assistant puisse continuer sans relire ces échanges. Le résumé remplace ces échanges : ce que tu n'y mets pas est perdu.

Garde uniquement ce qui servira pour la suite :
- `definitions` : les définitions établies (« client rentable = marge brute > 25 % »), une phrase chacune ;
- `figures` : les chiffres donnés dans les réponses, chacun avec `label` (ce qu'il mesure, client compris), `value` (un nombre, sans unité ni séparateur de milliers), `unit` (par exemple `€`, `k€`, `%`, `jours` ; chaîne vide si aucune) et `period` (la période ou la date, chaîne vide si aucune) ;
- `decisions` : les choix et préférences de l'utilisateur (période retenue, format des montants, périmètre) ;
- `open_questions` : les questions restées sans réponse ;
- `notes` : une ou deux phrases sur le sujet de la séance, ou une chaîne vide.

Règles :
- Reprends le résumé précédent s'il existe, complété par les nouveaux échanges ; une information plus récente remplace l'ancienne.
- N'invente rien : chaque élément vient des échanges ou du résumé précédent. Recopie les chiffres exactement.
- Phrases courtes, en français, sans répéter une information.

Réponds uniquement par un objet JSON de la forme :
{"definitions": ["..."], "figures": [{"label": "...", "value": 0, "unit": "", "period": ""}], "decisions": ["..."], "open_questions": ["..."], "notes": "..."}
