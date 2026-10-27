# Calibration du juge (calibration-numeric)

- Jeu : `eval/judge_calibration.jsonl` (20 réponses, dont 11 avec une affirmation contredite)
- Modèle du juge : aucun (contrôle numérique seul)

## Détection des réponses fausses

| Détecteur | Précision | Rappel | VP | FP | FN | VN |
|---|---:|---:|---:|---:|---:|---:|
| Contrôle numérique seul | 1.00 | 0.64 | 7 | 0 | 4 | 9 |

## Erreurs des détecteurs

- Contrôle numérique seul : c04 (wrong_entity, manquée), c10 (wrong_entity, manquée), c18 (wrong_number, manquée), c19 (wrong_entity, manquée)
