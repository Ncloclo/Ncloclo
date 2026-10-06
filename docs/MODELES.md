# Socle multi-modèles d'IA (étape 6 du prompt maître, deux versions)

Le bot ne dépend jamais d'une seule IA : un registre connaît les
fournisseurs, aucun modèle n'entre en service sans banc d'évaluation réussi,
un routeur choisit le mieux noté sur ses mesures réelles, chaque réponse est
vérifiée, un repli tracé prend le relais quand un modèle échoue, et sans IA
le bot répond seul.

```text
demande → invite enregistrée et active (version) → confidentialité
→ routeur : modèles approuvés au banc, permis, sains, dans les budgets,
  classés par la politique de l'usage (mesures réelles seulement)
→ appel → vérification de la réponse (montant inventé : rejetée)
→ échec ou rejet : repli tracé vers le suivant → aucun : MODEL_UNAVAILABLE,
  réponse intégrée sans IA
→ chaque appel noté : modèle, pourquoi lui, invite et sa version, durée,
  jetons, coût s'il est connu, erreur, repli, empreinte des données envoyées
```

Code : [`trendguard/modeles.py`](../trendguard/modeles.py). Tests :
[`tests/test_modeles.py`](../tests/test_modeles.py).

**Aujourd'hui, aucune clé d'IA n'est enregistrée sur ce PC** : le registre
connaît 8 fournisseurs (Claude, GPT, Gemini, DeepSeek, Mistral, Kimi,
Perplexity, Grok), tous « enregistrés, sans clé » ; le mode est « dégradé
sûr ». Rien n'est présenté comme disponible et aucune mesure n'est inventée :
sans appel ni banc, pas de chiffre.

## Ce qui passe par le socle

| Usage | Comment |
| --- | --- |
| Rachelle (panneau) | le modèle approuvé le mieux noté répond (politique « réponse rapide ») ; une réponse qui cite un montant absent des données du bot est rejetée ; si le modèle échoue ou est rejeté, le suivant ; si aucun ne répond, Rachelle répond elle-même ; le panneau dit quelle IA a répondu |
| Veille quotidienne | toutes les IA approuvées en parallèle ; une IA non évaluée ou au disjoncteur ouvert n'est pas appelée ; une réponse hors schéma est redemandée une fois puis revalidée ; désaccords détectés, jamais moyennés |
| Banc d'évaluation | lancé à la saisie d'une clé, ou par `python trendguard_bot.py modeles banc` : questions à réponse connue, justesse et durée mesurées, régression bloquée |

Une IA ne décide ni n'agit : sa réponse n'est qu'un texte. Aucun chemin ne
mène d'une IA à un ordre (un test le vérifie, et un autre montre que des IA
unanimes ne changent aucun trade) ; la règle du bot, puis la porte
d'exécution, décident comme avant.

## Cycle de vie d'un modèle

```text
enregistré (sans clé) → clé saisie → banc d'évaluation
→ réussi (justesse ≥ 75 %, sans régression) : approuvé → en service
→ 3 échecs de suite : dégradé (15 minutes à l'écart, puis un essai)
→ banc raté, régression ou nouveau modèle : à évaluer, jamais employé
```

Le banc a 8 questions à réponse connue : faits du bot (4), finance (taille
d'une position, hausse en pour cent), calcul, et un piège à invention (le
cours d'hier, que les faits ne donnent pas : la bonne réponse est
« inconnu »). Un banc dont des appels échouent (réseau) n'est pas concluant
et ne retire rien. Un nouveau modèle d'un fournisseur (par exemple
`VEILLE_MODEL_CLAUDE` changé) repasse le banc et doit faire au moins aussi
bien, à 25 points près, que le précédent.

## Commandes et réglages

- `python trendguard_bot.py modeles` : mode, chaque modèle avec sa fiche
  (justesse au banc par catégorie, fiabilité, latence, coût, confidentialité),
  mesures des 24 dernières heures, derniers appels (quel modèle, pourquoi,
  quel repli).
- `python trendguard_bot.py modeles banc` : banc d'évaluation de tous les
  modèles configurés (celui de Rachelle compris).
- `python trendguard_bot.py watch set-key claude` : ajouter une IA (clé
  saisie masquée, jamais affichée ni notée), puis son banc aussitôt.
- Modèle local (Ollama, vLLM, llama.cpp) : `TG_LLM_LOCAL_URL` dans `.env`,
  par exemple `http://127.0.0.1:11434/v1/chat/completions`, et
  `TG_LLM_LOCAL_MODEL` (défaut `llama3.1`), puis `modeles banc`. L'adresse
  doit être celle de ce PC ou du réseau privé : sinon le modèle « local » est
  refusé.
- `TG_LLM_JETONS_JOUR` : jetons permis par jour aux IA extérieures (défaut
  300 000).
- `TG_LLM_PRIX_CLAUDE=3/15` (un par fournisseur) : le prix que vous lisez
  sur le site du fournisseur, en dollars par million de jetons (entrée/sortie).
  Sans prix déclaré, le coût reste « inconnu » : il n'est jamais estimé.
- `TG_LLM_BUDGET_USD_JOUR` : plafond de coût par jour (seulement sur les
  appels dont le prix est déclaré) ; un plafond mal écrit vaut 0.

## Garanties

| Exigence de l'étape 6 | Dans le code |
| --- | --- |
| Registres, manifestes, identifiant stable (§3-6 ; §4-7) | fournisseur, modèle, identifiant `fournisseur:modèle`, type (extérieur ou local), adresse, capacités, confidentialité acceptée, configuré ou non ; jamais la clé |
| Cycle de vie et gouvernance (§41, §44-45 ; §46) | aucun modèle en service sans banc réussi ; banc raté ou régression : écarté ; état du fournisseur (ACTIVE, DEGRADED, DISABLED, BLOCKED, UNKNOWN) |
| Routeur et politique (§7-10 ; §8-11) | note pondérée par usage (Rachelle : justesse, réussite, rapidité ; veille : justesse, réussite), sur mesures réelles seulement, « non mesurée » comptant pour neutre ; raison notée à chaque appel |
| Santé, disjoncteur, repli, mode dégradé (§11-13, §53, §63-64 ; §12-15, §70) | réussite, médiane et 95e centile ; disjoncteur fermé, ouvert, demi-ouvert ; repli tracé ; aucun modèle permis : MODEL_UNAVAILABLE, jamais un modèle non permis à la place ; réponse intégrée |
| Invites versionnées (§17-18 ; §27-28) | chaque invite a une version et l'empreinte exacte de son texte ; changée sans nouvelle version : refusée, l'IA n'est pas appelée ; version notée avec chaque appel |
| Sorties vérifiées, invention (§16, §57-58 ; §21) | veille : JSON exigé, redemandé une fois, événements sans source réelle écartés ; Rachelle : un montant absent des données du bot rejette la réponse ; le calcul de référence reste celui du bot |
| Jetons, coût, budgets (§22-23 ; §31-33) | jetons notés quand ils sont donnés (jamais estimés) ; coût = jetons × prix que vous déclarez ; plafonds de jetons et de coût par jour |
| Confidentialité, modèle local, sécurité (§31-34 ; §38-43) | un texte qui ressemble à un secret est LOCAL_ONLY : jamais envoyé dehors ; « modèle local » vers Internet refusé ; liste fermée des fournisseurs et de leurs adresses ; aucune IA n'a d'outil ; clés seulement dans `.env` |
| Plusieurs modèles, consensus, désaccord (§25-27 ; §16-19, §65) | la veille interroge toutes les IA approuvées ; consensus avec état (fort, modéré, faible, aucun, conflit) et scores ; un désaccord net est un contrat (positions de chacun, gravité, issue NO_DECISION) : pas d'avis moyen, alerte d'information pour une crypto détenue |
| Banc, régression (§38-41 ; §44-46) | banc versionné (numéro et empreinte des questions) ; justesse par catégorie ; résultats gardés (`llm_benchmarks`, en ajout seulement) |
| Fiche, mesures, trace (§43, §46-47, §71 ; §48-49, §91) | fiche de chaque modèle et mesures (demandes, appels, échecs, replis, réponses rejetées, sorties hors schéma, jetons, coût, latences) tirées de la trace `llm_executions` ; rapport quotidien, page Veille, Rachelle |
| Sécurité financière (§54-56 ; §2, §64-66, §75, §85) | aucune IA sur le chemin des ordres ; « pas de trade » reste une issue valide ; seul un retrait annoncé par Binance bloque un achat |

## Pas appliqué ici, et pourquoi

- **Notes de qualité « raisonnement, code, vision… » par modèle** : rien
  n'est mesurable sans clé ; le routeur emploie la justesse au banc, la
  réussite et la rapidité mesurées, rien d'autre.
- **Débat, juge et synthèse entre IA, comité financier d'IA** : il faut au
  moins deux IA configurées ; le débat existe déjà entre les agents du comité
  (étape 5), et la veille compare les IA et montre leurs désaccords.
- **A/B testing, file de priorités, gouverneur GPU** : un seul utilisateur,
  un seul PC, quelques appels par jour ; rien à répartir.
- **Cache, embeddings, reranking, vision, voix, flux, outils pour les IA** :
  aucun usage dans le bot ; ne donner aucun outil à une IA est une sécurité.
- **Pydantic, API REST, dossier `multi_llm_core/`** : un seul PC ; des
  classes typées, une trace SQLite et des commandes suffisent (comme aux
  étapes 3 à 5).
- **Résidence des données, notes de confiance des fournisseurs** : la liste
  des fournisseurs est fermée dans le code ; seul le modèle local est
  réglable, et il doit rester local.
- **Prix des fournisseurs dans le code** : ils changent ; c'est vous qui les
  déclarez, sinon le coût reste inconnu.
