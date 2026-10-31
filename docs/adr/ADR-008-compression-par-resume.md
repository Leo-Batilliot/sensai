# ADR-008 — Compression de l'historique par résumé structuré plutôt que troncature

- **Statut** : acceptée ; mesurée en version réduite le 31/10/2026 (EVAL-10)
- **Date** : 20/10/2026 (D-01, M2 via CORE-13) ; mise en œuvre le 27/10/2026 ; rédigée le 31/10/2026
- **Auteur** : P1 (G.WI)
- **Voir aussi** : [PROJECT.md](../../PROJECT.md) §6.10, `src/sensai/core/context.py`, [`docs/user-stories/M2.md`](../user-stories/M2.md)

## Contexte

Claire prépare son comité pendant une heure : la conversation s'allonge, avec les résultats SQL, la mémoire, le plan de la note et le profil dans chaque prompt. Au-delà de `num_ctx` (16384 tokens), **Ollama coupe le prompt en silence, par le début** : le prompt système part en premier, avec le schéma et les règles. L'agent continue de répondre, mais sans ses consignes.

Les faits qui doivent survivre sont les définitions, les chiffres déjà donnés et les choix de l'utilisateur (une période, un périmètre).

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Laisser faire Ollama** | Aucun code | Perte silencieuse du prompt système : comportement imprévisible en fin de séance |
| **B. Troncature par le moteur** : retirer les échanges les plus anciens | Gratuite, prévisible, le prompt système est protégé | Tout ce qui sort de la fenêtre est oublié, y compris un chiffre vérifié ou un choix de l'utilisateur |
| **C. Augmenter `num_ctx`** | Rien à oublier pendant plus longtemps | Plus de VRAM (déjà 7,4 Go sur 10), plus lent, et ne fait que repousser le problème |
| **D. Résumé en texte libre** des échanges anciens | Garde le sens de la conversation | Impossible à vérifier : le modèle peut omettre ou déformer un chiffre |
| **E. Résumé structuré + contrôle des chiffres par le code** | Garde l'essentiel, et les derniers chiffres vérifiés ne peuvent pas être perdus | Un appel au modèle de plus quand il se déclenche ; plus de code à tester |

## Décision

**Option E**, avec **B gardée comme témoin et comme repli** (`[context] strategy = "truncate"`).

Avant le premier appel au modèle de chaque tour, `Compactor.fit` estime la taille du prompt (prompt système, mémoire, profil, plan de la note, historique, question). Au-delà de `budget_ratio × num_ctx` (0,75), les échanges les plus anciens, hors des `keep_recent_turns` derniers (4), quittent l'historique :

1. Le modèle les résume en **sortie structurée** (T5) : `definitions`, `figures` (libellé, valeur, unité, période), `decisions`, `open_questions`, `notes`. Le résumé est **glissant** : le suivant reprend le précédent.
2. **Le code remet les chiffres vérifiés** : chaque chiffre clé que le juge a validé dans ces échanges et que le résumé ne donne pas est ajouté tel quel (`keep_figures`, à la tolérance du contrôle numérique). Les 15 derniers chiffres vérifiés passent ainsi de résumé en résumé (`MAX_VERIFIED`).
3. Le résumé est rendu en texte compact, plafonné à `summary_max_tokens` (500), juste après le prompt système.
4. **Repli** : un résumé qui échoue (Ollama indisponible, JSON encore invalide après réparation) ou plus long que ce qu'il remplace laisse place à la troncature pour ce tour. C'est journalisé, jamais une erreur pour l'utilisateur.

L'estimation compte 3,5 caractères par token, puis elle est recalée à chaque réponse sur le `prompt_eval_count` réel d'Ollama, avec des bornes, car sous-estimer est le côté dangereux. L'événement `context_compacted` dit à l'interface combien d'échanges ont été résumés et le gain en tokens. La jauge de contexte de la barre d'état redescend.

## Conséquences

- **Le moteur choisit ce qui sort, avant Ollama** : le prompt système passe en premier, puis les échanges récents. Si les échanges récents dépassent encore le budget à eux seuls, ils sortent à leur tour, du plus ancien au plus récent ; si rien ne peut plus sortir, c'est journalisé.
- **Un chiffre vérifié survit au résumé**, par construction du code et non par la bonne volonté du modèle, dans la limite des 15 derniers. Dans une séance riche en chiffres, un chiffre vérifié ancien peut sortir de cette liste.
- **Les définitions du glossaire n'ont pas besoin d'y être** : la mémoire (M3) les réinjecte à chaque tour.
- **Coût** : la compression ajoute un appel au modèle de quelques secondes. Elle ne se déclenche qu'au-delà de 75 % du contexte, et l'interface l'affiche pour que l'attente soit expliquée.
- **Mesuré (EVAL-10, version réduite)** : sur 5 faits plantés puis redemandés, la troncature en retrouve 0, le résumé 2 (l'objectif chiffré et la définition donnés par l'utilisatrice). Un contrôle a été coupé par le délai. Deux faits restent perdus avec le résumé : un **choix de périmètre** qui n'est pas réappliqué, et un chiffre gardé dans le résumé mais confondu avec deux chiffres aux libellés proches. Chaque compaction coûte environ 19 s. Le résumé fait mieux que la troncature, sans être une mémoire parfaite ([`EVALUATION.md`](../EVALUATION.md) §5). Ce que l'utilisatrice veut voir durer d'une séance à l'autre relève de la mémoire (M3), pas du résumé.
