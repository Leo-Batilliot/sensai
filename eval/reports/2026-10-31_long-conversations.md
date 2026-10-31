# Conversations longues : troncature contre résumé (2026-10-31)

- Jeu : `eval/long_conversations.jsonl` (2 conversations, 5 faits plantés)
- Modèle : qwen3:8b, juge : qwen3:8b, `num_ctx` 16384, prompt v4, graine 42, date de référence 2026-10-20
- Budget : `budget_ratio` 0.29 (limite estimée 4751 tokens), `keep_recent_turns` 2, `summary_max_tokens` 500
- Mémoire : outils mémoire retirés, aucun contexte mémoire injecté (seul l'historique ou son résumé peut porter un fait)

## Résultats

| Stratégie | Faits retrouvés | Par nouvelle requête | Perdus | Interrompus (délai) | Compactions | Durée moyenne d'une compaction | Tokens du prompt (moyenne) | Tour p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `truncate` | 0 / 5 | 0 | 5 | 0 | 4 | 0,0 s | 4400.9 | 33,0 s |
| `compress` | 2 / 5 | 0 | 2 | 1 | 4 | 18,8 s | 4466.2 | 38,2 s |

## Faits

| Stratégie | Conversation | Fait | Attendu | Verdict | Extrait de la réponse |
|---|---|---|---:|---|---|
| `truncate` | conv-1 | objectif | 480000 | perdu | L'objectif de chiffre d'affaires du trimestre n'est pas disponible dans les données de l'entreprise. Veuillez consulter le plan de travail o |
| `truncate` | conv-1 | gros-client | 17 | perdu | Nous avons **7 gros clients** (segment 'GE'). |
| `truncate` | conv-1 | ca-trimestre | 209626 | perdu | Le chiffre d'affaires HT du trimestre n'est pas disponible dans les données de l'entreprise. Veuillez consulter la table `invoices` pour cal |
| `truncate` | conv-2 | restauration | 9 | perdu | Nous avons reçu **165 commandes** le mois dernier (septembre 2026). |
| `truncate` | conv-2 | clients-actifs | 260 | perdu | Le nombre de clients actifs n'a jamais été mentionné explicitement dans les réponses précédentes. Pour obtenir ce chiffre, une requête sur l |
| `compress` | conv-1 | objectif | 480000 | retrouvé | Notre objectif de chiffre d'affaires HT pour ce trimestre (octobre à décembre 2026) est de **480 000 €**. |
| `compress` | conv-1 | gros-client | 17 | retrouvé | Nous avons **17 gros clients** selon le critère défini : chiffre d'affaires HT > 30 000 € sur l'historique (factures annulées exclues). |
| `compress` | conv-1 | ca-trimestre | 209626 | interrompu |  |
| `compress` | conv-2 | restauration | 9 | perdu | Le mois dernier (septembre 2026), **154 commandes** ont été reçues et validées. |
| `compress` | conv-2 | clients-actifs | 260 | perdu | Le nombre de clients actifs mentionné au début était de **20** dans le secteur de la Restauration. |

## Compactions

| Stratégie | Conversation | Tour | Échanges retirés | Tokens avant → après | Durée | Début du résumé |
|---|---|---:|---:|---|---:|---|
| `truncate` | conv-1 | 7 | 4 | 4903 → 4508 | 0,0 s | Troncature : 4 échange(s) retiré(s) sans résumé. |
| `truncate` | conv-1 | 8 | 2 | 4981 → 4569 | 0,0 s | Troncature : 2 échange(s) retiré(s) sans résumé. |
| `truncate` | conv-2 | 8 | 5 | 4840 → 4338 | 0,0 s | Troncature : 5 échange(s) retiré(s) sans résumé. |
| `truncate` | conv-2 | 11 | 3 | 4760 → 4437 | 0,0 s | Troncature : 3 échange(s) retiré(s) sans résumé. |
| `compress` | conv-1 | 7 | 4 | 4822 → 4668 | 16,8 s | Chiffres : Chiffre d'affaires HT de ce trimestre : 209625.54 € (octobre à décembre 2026) ; Nombre de clients actifs par  |
| `compress` | conv-1 | 8 | 2 | 5045 → 4721 | 20,5 s | Chiffres : Chiffre d'affaires HT de Julien Marchand : 655178.03 € (octobre à décembre 2026) ; Chiffre d'affaires HT de M |
| `compress` | conv-1 | 12 | 3 | 4812 → 4665 | 25,0 s | Chiffres : Factures en retard HT - Transports Gilbert : 2008.91 € (octobre à décembre 2026) ; Factures en retard HT - Ag |
| `compress` | conv-2 | 7 | 4 | 4789 → 4472 | 13,0 s | Chiffres : clients actifs dans le secteur de la Restauration : 20 ; montant total HT des factures en retard : 11340 € ;  |
