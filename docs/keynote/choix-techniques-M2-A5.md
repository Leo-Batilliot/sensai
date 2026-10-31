# Slides « choix techniques » : M2 et A5

Contenu fourni par P1 pour KEY-03 (DOC-11). Dans la keynote, M2 tombe dans le groupe « mémoire et contexte » (avec M3 et M4) et A5 dans le groupe « sécurité » (avec A3 et EV4) : environ 35 s par groupe, donc une slide et une phrase par fonctionnalité. Le détail est dans les ADR, pour les questions du jury.

## Slide M2 — Longues séances sans perte de contexte

**Le problème** : au-delà de la fenêtre de contexte, Ollama coupe le prompt en silence, par le début, prompt système compris.

| | |
|---|---|
| **Considéré** | Laisser faire Ollama · tronquer l'historique · augmenter `num_ctx` · résumé en texte libre |
| **Écarté** | La troncature seule (oublie un chiffre vérifié) · un `num_ctx` plus grand (VRAM déjà à 7,4 Go sur 10, ne fait que repousser) · le texte libre (impossible à vérifier) |
| **Retenu** | Résumé **structuré** des échanges anciens au-delà de 75 % du contexte, et **le code remet les chiffres vérifiés** que le modèle aurait oubliés |
| **Mesuré** | Faits plantés puis redemandés (EVAL-10, version réduite) : **troncature 0 sur 5, résumé 2 sur 5**, et 1 contrôle coupé par le délai |
| **Limite** | Environ 19 s par résumé ; un choix de périmètre n'est pas réappliqué, et des chiffres aux libellés proches se confondent |

**À dire** : « Quand la conversation devient trop longue, on ne laisse pas le modèle oublier en silence : on résume. On l'a mesuré : la troncature perd tout ce qu'on a dit au début, le résumé garde les chiffres et les définitions donnés par l'utilisateur, mais pas encore un choix de périmètre. »

**Si le jury demande** : pourquoi ne pas simplement augmenter `num_ctx` ? → plus de VRAM, plus lent, et ça repousse le problème sans le régler. Comment savez-vous que le résumé garde l'essentiel ? → par la mesure EVAL-10, avec un contrôle par le code et la mémoire désactivée pour isoler l'effet du résumé. Elle montre aussi ce qu'il rate ([ADR-008](../adr/ADR-008-compression-par-resume.md), [EVALUATION.md](../EVALUATION.md) §5).

## Slide A5 — Un profil ne fait que restreindre

**Le problème** : Claire ne doit rien modifier, Karim supprime des clients, Inès exporte des fichiers ; même agent, même conversation.

| | |
|---|---|
| **Considéré** | Un prompt par profil · filtrer les outils montrés au modèle · donner des droits propres au profil · un agent par profil |
| **Écarté** | Le prompt seul (une consigne, pas une barrière) · le filtre seul (le modèle peut inventer un nom d'outil) · des droits propres au profil (le fichier deviendrait une porte d'entrée) · un agent par profil (perd l'historique) |
| **Retenu** | **Deux barrières** : le modèle ne voit que les outils du profil, et la politique refuse tout outil hors profil même inventé. **Jamais d'élévation** : un profil ne lève aucune confirmation |
| **Limite** | Donner plus de droits demande de changer la configuration, pas le profil (c'est voulu) ; l'attaque d'escalade n'est pas encore dans la suite adversariale |

**À dire** : « Changer de profil change ce que l'agent peut faire, jamais ce qu'il peut faire sans vous : la suppression de Karim demande le profil ADV et son accord. »

**Si le jury demande** : qu'est-ce qui empêche un profil de s'attribuer des droits ? → le droit effectif est l'intersection du profil et de la configuration ; la politique applique toujours la règle la plus stricte ([ADR-009](../adr/ADR-009-profil-restreint-seulement.md)).
