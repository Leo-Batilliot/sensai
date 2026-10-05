# BlocSQL

Bloc qui montre la requête SQL exécutée, avec mise en forme, bouton copier et lien vers le résultat.

## Annotations
1. **En-tête** : type d'opération (lecture seule / écriture), outil et durée.
2. **Actions** : copier le SQL, aller au tableau de résultats.
3. **Mémoire appliquée** : chips des entrées du glossaire utilisées pour écrire la requête (acte 2 de la démo).

## États
- Replié (par défaut dans un message) : une ligne tronquée.
- Écriture : bordure rouge, chip « écriture · DELETE », « en attente de confirmation ».

## Événements
`sql_generated`.
