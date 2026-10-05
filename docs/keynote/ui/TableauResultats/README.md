# TableauResultats

Tableau des lignes renvoyées par la requête, triable côté navigateur.

## Annotations
1. **En-tête** : nombre de lignes, de colonnes, tri actif ; export CSV.
2. **Tri** : clic sur une colonne, sans nouvel appel au serveur.
3. **Limite** : 200 lignes maximum (`row_limit`), message si tronqué.

## Règles
- Nombres alignés à droite, chiffres à largeur fixe, format français (`48 712,40`).
- Noms de colonnes affichés tels que renvoyés par le SQL.

## Événements
`tool_result.data` (colonnes + lignes de `run_select`).

## User story
US-X1-01.
