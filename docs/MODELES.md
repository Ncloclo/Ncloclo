# Socle multi-modèles d'IA (étape 6 du prompt maître)

Le bot ne dépend jamais d'une seule IA : un registre connaît les
fournisseurs, un routeur choisit le meilleur modèle permis, un repli tracé
prend le relais quand un modèle échoue, et sans IA le bot répond seul.

```text
demande → confidentialité (un secret ne sort jamais du PC)
→ routeur : modèles configurés, permis, sains, dans le budget du jour
→ appel → échec ? repli tracé vers le suivant → aucun ? réponse intégrée
→ chaque appel noté : modèle, pourquoi lui, invite et sa version, durée,
  jetons, erreur, repli, empreinte des données envoyées
```

Code : [`trendguard/modeles.py`](../trendguard/modeles.py). Tests :
[`tests/test_modeles.py`](../tests/test_modeles.py).

**Aujourd'hui, aucune clé d'IA n'est enregistrée sur ce PC** : le registre
connaît 8 fournisseurs (Claude, GPT, Gemini, DeepSeek, Mistral, Kimi,
Perplexity, Grok), tous « non configurés ». Rien n'est présenté comme
disponible et aucune mesure n'est inventée : sans appel, pas de chiffre.

## Ce qui passe par le socle

| Usage | Comment |
| --- | --- |
| Rachelle (panneau) | la meilleure IA configurée répond (Claude de préférence) ; si elle échoue, la suivante ; si aucune ne répond, Rachelle répond elle-même à partir de l'état du bot ; le panneau dit quelle IA a répondu |
| Veille quotidienne | toutes les IA configurées en parallèle ; une IA au disjoncteur ouvert n'est pas appelée ; chaque appel est noté ; les désaccords entre IA sont détectés |
| Banc d'évaluation | `python trendguard_bot.py modeles banc` : questions à réponse connue posées à chaque modèle configuré, justesse et durée mesurées, régression signalée |

Une IA ne décide ni n'agit : sa réponse n'est qu'un texte. Aucun chemin ne
mène d'une IA à un ordre (un test le vérifie) ; la règle du bot, puis la
porte d'exécution, décident comme avant.

## Commandes et réglages

- `python trendguard_bot.py modeles` : modèles connus, configurés ou non,
  santé mesurée, derniers appels (quel modèle, pourquoi, quel repli).
- `python trendguard_bot.py modeles banc` : banc d'évaluation.
- `python trendguard_bot.py watch set-key claude` : ajouter une IA (clé
  saisie masquée, jamais affichée ni notée).
- Modèle local (Ollama, vLLM, llama.cpp) : `TG_LLM_LOCAL_URL` dans `.env`,
  par exemple `http://127.0.0.1:11434/v1/chat/completions`, et
  `TG_LLM_LOCAL_MODEL` (défaut `llama3.1`). L'adresse doit être celle de ce
  PC ou du réseau privé : sinon le modèle « local » est refusé.
- `TG_LLM_JETONS_JOUR` : jetons permis par jour aux IA extérieures (défaut
  300 000).

## Garanties

| Exigence de l'étape 6 | Dans le code |
| --- | --- |
| Registre des fournisseurs et des modèles, manifestes (§4-7) | fournisseur, modèle, type (extérieur ou local), adresse, capacités (texte, JSON, recherche web), confidentialité acceptée, configuré ou non ; jamais la clé |
| Routeur, raison du choix (§8-11) | modèles écartés avec leur raison (non configuré, confidentialité, capacité, disjoncteur, budget) ; classement par réussite mesurée, puis préférence, puis ordre du registre ; la raison est notée à chaque appel |
| Santé, disjoncteur (§12-13) | appels, échecs, durée médiane et 95e centile sur les 20 derniers appels ; après 3 échecs de suite le modèle est mis de côté 15 minutes, puis un essai, refermé au premier succès ; partagé par le bot et le panneau |
| Repli tracé, dégradation contrôlée (§14-15, §70) | modèle d'origine, raison de l'échec, modèle de repli, résultat ; jamais silencieux ; sans IA, réponse intégrée et veille par annonces officielles et mots-clés, sans aucune autonomie de plus |
| Plusieurs modèles, consensus, désaccord (§16-19, §65) | la veille interroge toutes les IA en parallèle ; consensus pondéré par la fiabilité mesurée sur les cours réels ; quand plusieurs IA répondent, une alerte doit être confirmée par deux ; sur une crypto où elles divergent nettement, pas d'avis moyen : le désaccord est montré (et signalé aussi pour le climat) |
| Sorties structurées (§21) | la veille exige un JSON conforme au schéma ; chaque événement doit citer une source réelle, sinon il est écarté |
| Versions des invites (§27-28) | version = empreinte du texte de l'invite, notée à chaque appel ; un test tient les versions : changer une invite passe par une revue |
| Jetons, budget (§31-33) | jetons d'entrée et de sortie notés quand le fournisseur les donne (jamais estimés) ; budget du jour : au-delà, plus d'appel extérieur |
| Confidentialité, modèles locaux (§38-39, §84) | un texte qui ressemble à une clé ou un mot de passe est LOCAL_ONLY : jamais envoyé dehors, seulement à un modèle local ; les données du bot ne vont qu'aux IA dont vous avez mis la clé |
| Sécurité des modèles (§42-43) | liste fermée des fournisseurs et de leurs adresses dans le code ; un « modèle local » vers Internet est refusé ; une IA ne reçoit aucun outil ; clés seulement dans `.env`, saisies masquées, jamais dans les traces |
| Banc, régression (§44-46) | questions à réponse connue (faits du bot, calcul) ; justesse et durée ; une baisse de plus de 25 points par rapport au banc précédent est signalée |
| Trace, observabilité (§48-49, §78, §91) | table `llm_executions` (fichier `trendguard_modeles.db`), en ajout seulement ; rapport quotidien (« Modèles d'IA »), page Veille, Rachelle et la commande `modeles` |
| Sécurité financière (§2, §64-66, §75, §85) | aucune IA sur le chemin des ordres ; seul un retrait annoncé par Binance bloque un achat |

## Pas appliqué ici, et pourquoi

- **Coût en dollars** : les prix des fournisseurs changent ; le bot compte
  les jetons et plafonne leur nombre par jour ; la facture du fournisseur
  fait foi. Aucun prix n'est inventé.
- **Notes de qualité par modèle (raisonnement, finance…)** : rien n'est
  mesurable sans clé ; le routeur emploie la réussite mesurée, le banc la
  justesse mesurée.
- **Débat, juge et synthèse entre IA** : il faut au moins deux IA
  configurées ; le débat existe déjà entre les agents du comité (étape 5),
  et la veille compare les IA et montre leurs désaccords.
- **Cache, embeddings, reranking, vision, voix, flux, outils pour les IA** :
  aucun usage dans le bot ; ne donner aucun outil à une IA est une sécurité.
- **Pydantic, API REST, dossier `multi_llm/`** : un seul PC ; des classes
  typées, une trace SQLite et des commandes suffisent (comme aux étapes 3 à
  5).
- **Résidence des données, notes de confiance des fournisseurs** : la liste
  des fournisseurs est fermée dans le code ; seul le modèle local est
  réglable, et il doit rester local.
- **Cycle brouillon → production des invites** : les invites vivent dans le
  code ; un changement passe par une revue (demande de fusion) et par le test
  qui tient leur version.
