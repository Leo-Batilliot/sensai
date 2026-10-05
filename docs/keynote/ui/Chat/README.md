# Conversation

Fil de chat : la question de l'utilisateur, la réponse en streaming et les états intermédiaires de l'agent.

## Annotations
1. **Message utilisateur** : bulle à droite, texte brut.
2. **État de l'agent** : chip qui suit les événements (`thinking` → « Réfléchit », `tool_call` → « Appelle un outil : X », `confirmation_required` → « En attente de votre confirmation »).
3. **Réponse en streaming** : Markdown rendu au fil des `token`, curseur clignotant, auto-scroll (stoppé si l'utilisateur remonte).
4. **Replis** : SQL, résultats et badge du juge sous la réponse ; repliés par défaut sauf les résultats.
5. **Saisie** : Entrée envoie, Maj+Entrée saute une ligne, entrée vide ignorée. Pendant la génération, « Stop » envoie `cancel` ; la réponse est marquée « interrompue ».

## Événements
`turn_started`, `thinking`, `token`, `tool_call`, `message_done`, `error`.

## User story
US-X1-01.
