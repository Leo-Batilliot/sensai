# ADR-009 — Un profil d'agent ne fait que restreindre, jamais élever

- **Statut** : acceptée
- **Date** : 21/10/2026 (contrats v1.1, §3.7) ; mise en œuvre le 27/10/2026 (PERS-01) ; rédigée le 31/10/2026
- **Auteur** : P1 (G.WI), avec P2 (T.BE) pour la politique
- **Voir aussi** : [PROJECT.md](../../PROJECT.md) §6.12, `src/sensai/core/personas.py`, `src/sensai/tools/policy.py`, [`docs/user-stories/A5.md`](../user-stories/A5.md)

## Contexte

Le même agent sert trois personnes (A5) : Claire (contrôle de gestion) ne doit rien modifier, Karim (ADV) supprime des clients, Inès (administration) exporte des fichiers. On change de profil en cours de séance sans perdre l'historique. Les profils sont des fichiers TOML dans `personas/`, que n'importe qui peut éditer.

La question du jury est attendue : qu'est-ce qui empêche un profil, ou un modèle qui se fait passer pour un autre profil, de s'attribuer des droits ?

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Un prompt par profil**, sans filtre d'outils | Simple : « tu n'as pas le droit d'écrire » | Le modèle peut quand même appeler l'outil interdit, par erreur ou sous injection : la règle n'est qu'une consigne |
| **B. Le profil filtre les outils montrés au modèle** | Le modèle ne connaît pas les outils interdits | Un modèle peut inventer le nom d'un outil qu'on ne lui a pas montré ; rien ne l'arrête ensuite |
| **C. Le profil porte ses propres droits** : il peut lever une confirmation ou autoriser un outil refusé par `[hitl]` | Souple (« Karim n'a pas besoin de confirmer ») | Un fichier de profil devient une porte d'entrée : qui l'édite contourne la confirmation (A3) |
| **D. Deux barrières, et le profil ne peut que retirer** : filtre des outils + règle de politique ; jamais d'élévation | Le droit effectif est toujours l'intersection du profil et de la configuration | Moins souple : pour donner plus de droits, il faut changer la configuration, pas le profil |

## Décision

**Option D.**

- **Barrière 1, ce que le modèle voit** : seuls les outils dont le nom correspond aux motifs `allow` du profil (et à aucun motif `deny`) sont envoyés au modèle.
- **Barrière 2, ce que la politique accepte** : la première règle de `policy.evaluate` refuse tout outil hors du profil (`TOOL_DENIED`), même si le modèle invente l'appel. Le refus revient au modèle comme un résultat d'outil.
- **Le profil ne fait que restreindre** : il ne peut ni lever une confirmation, ni modifier `[hitl]`, ni autoriser un outil de `[hitl] deny`. La politique applique toujours la règle la plus stricte ; le profil n'ajoute que des refus.
- Le texte du profil (rôle, ton, périmètre, consignes) remplit le trou `$persona` du prompt. Chaque champ a une longueur maximale, pour que le bloc reste sous environ 300 tokens (M2).
- Un fichier invalide arrête l'application au démarrage, avec le nom du fichier et du champ.

Les trois profils livrés : `analyste` (lecture de la base, mémoire et note, aucune écriture SQL ni fichier), `adv` (+ `execute_write`), `admin` (+ `file_write`). Les écritures restent soumises à confirmation dans tous les cas.

## Conséquences

- **Changer de profil ne lève jamais une confirmation** : la suppression de l'acte 4 demande le profil ADV **et** l'accord de Karim.
- **Un fichier de profil modifié ne peut pas élever les droits** au-delà de la configuration : au pire, il ouvre un outil existant, dont les écritures restent soumises à la confirmation et aux autres garde-fous (garde SQL, permissions de fichiers, `[hitl] deny`).
- **Testé contre le pire cas** : la suite adversariale en CI rejoue les attaques avec un profil qui garde tous les outils, pour vérifier que chaque attaque est arrêtée par sa propre couche (garde SQL, confirmation, permissions de fichiers) et pas seulement par le filtre du profil ([`SECURITY.md`](../SECURITY.md)).
- **Le changement de profil garde le contexte** : un message « Profil actif : ADV (Copilote de Karim, responsable ADV) » est inséré dans l'historique, et le prompt suivant porte le nouveau profil.
- **Coût** : donner plus de droits demande de modifier `[hitl]`, ce qui est voulu : c'est une décision d'administrateur, pas de profil.
