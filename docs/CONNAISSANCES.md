# Recherche et connaissances (critères AC-001 à AC-080)

Mesuré le 2026-10-09 par `python trendguard_bot.py recherche` (étape 22 du
prompt maître, [`RECHERCHE.md`](RECHERCHE.md)). Une information extérieure n'est
jamais tenue pour vraie d'office.

## Verdict

- **PRÊT** (READY).
- Note 100,0/100 (prête) ; critères P0 non satisfaits : 0.
- 12 sources (2 officielles) ; 0 affirmation(s) sur lesquelles le bot agit ; 1
  contradiction(s) aujourd'hui.

## Sources

| Source | rang | usage | fiabilité mesurée | dernière lecture | confiance |
| --- | --- | --- | --- | --- | --- |
| Annonces de Binance | 1 (officielle) | peut empêcher un achat (retrait de la cote) | — | il y a 25,2 h | 80 |
| Bougies de Binance | 1 (officielle) | décision de la règle | — | il y a 0,0 h | 80 |
| Calendrier économique | 2 (professionnelle) | information (prudence avant une annonce) | — | il y a 25,2 h | 70 |
| Tendances CoinGecko | 2 (professionnelle) | avis du savoir | observation (1 semaine(s)) | il y a 0,1 h | 70 |
| Fear & Greed | 2 (professionnelle) | avis du savoir (jugé sur les vrais cours) | hasard (129 semaine(s)) | il y a 1,2 h | 62 |
| Presse spécialisée | 3 (presse) | avis du savoir | observation (3 semaine(s)) | il y a 0,5 h | 60 |
| Google Actualités | 3 (presse) | avis du savoir ; reprend Presse spécialisée | observation (1 semaine(s)) | il y a 0,1 h | 60 |
| Bing Actualités | 3 (presse) | avis du savoir ; reprend Presse spécialisée | observation (1 semaine(s)) | il y a 19,2 h | 44 |
| Reddit | 4 (communauté) | avis du savoir | — | il y a 1,0 h | 50 |
| Hacker News | 4 (communauté) | avis du savoir | observation (2 semaine(s)) | il y a 4,3 h | 50 |
| StockTwits | 4 (communauté) | avis du savoir | observation (1 semaine(s)) | il y a 0,1 h | 50 |
| IA de la veille | 4 (communauté) | conseil seulement, jamais une action | — | — | 30 |

## Affirmations sur lesquelles le bot agit

Aucune aujourd'hui : ni retrait de Binance, ni report d'achat.

## Contradictions du jour

- BTC : en hausse pour StockTwits, Tendances CoinGecko ; en baisse pour Google
  Actualités (contestée : aucune décision n'en dépend)

## Familles

| Famille | poids | note |
| --- | --- | --- |
| qualité des données | 15 % | 100 |
| vérification des sources | 15 % | 100 |
| qualité des connaissances | 15 % | 100 |
| sécurité | 15 % | 100 |
| recherche documentaire | 10 % | 100 |
| provenance | 10 % | 100 |
| reproductibilité | 10 % | 100 |
| observabilité | 5 % | 100 |
| performance | 5 % | 100 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Recherche simple | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-002 Recherche complexe | P2 | sans objet | pas de recherche ouverte : des sources fixes, lues chaque 15 minutes |
| AC-003 Planification de la recherche | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-004 Sous-questions | P2 | sans objet | pas de question ouverte à décomposer |
| AC-005 Plusieurs sources | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-006 Sources classées | P0 | conforme | 2 officielle, 3 professionnelle, 3 presse, 4 communauté ; 1 test(s) du dépôt |
| AC-007 Provenance des sources | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-008 Versions des sources | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-009 Documents lus | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-010 Reconnaissance de texte (OCR) | P2 | sans objet | aucun document à analyser : le bot lit des titres et des flux publics |
| AC-011 Tableaux extraits | P2 | sans objet | aucun document à analyser : le bot lit des titres et des flux publics |
| AC-012 Entités résolues (cryptos citées) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-013 Graphe de connaissances | P2 | sans objet | mémoire temporelle de l'étape 24 |
| AC-014 Contradictions détectées | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-015 Vérification des faits | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-016 Citations exactes | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-017 Confiance | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-018 Incertitude | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-019 Recherche augmentée (documents du dépôt) | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-020 Recherche hybride | P2 | sans objet | pas de moteur de recherche vectoriel : mots-clés seulement |
| AC-021 Reclassement | P2 | sans objet | pas de moteur de recherche vectoriel |
| AC-022 Réponse liée à sa preuve | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 Aucune invention | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-024 Budgets de recherche | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-025 Conditions d'arrêt | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-026 Recherche reproductible | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-027 Recherche tracée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-028 Surveillance continue | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-029 Fraîcheur des connaissances | P0 | conforme | 11 source(s) datée(s), toutes dans leur validité ; 1 test(s) du dépôt |
| AC-030 Changements détectés | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-031 Alertes | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-032 Plusieurs langues | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-033 Recherche scientifique | P2 | sans objet | hors du métier du bot |
| AC-034 Recherche technique | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-035 Recherche financière | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-036 Recherche de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-037 Recherche sur l'énergie | P2 | sans objet | hors du métier du bot |
| AC-038 Hiérarchie des sources | P0 | conforme | 2 officielle, 3 professionnelle, 3 presse, 4 communauté ; 1 test(s) du dépôt |
| AC-039 Diversité des sources | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Graphe de provenance | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-041 Contenu extérieur isolé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-042 Injection d'instructions refusée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-043 Document malveillant | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-044 Adresses internes protégées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-045 Débit limité | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-046 Délai maximal | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-047 Bac à sable | P2 | sans objet | le savoir tourne dans un processus séparé ; il ne lance aucun code lu |
| AC-048 Logiciels malveillants | P2 | sans objet | aucun fichier téléchargé n'est exécuté ni ouvert |
| AC-049 Fraîcheur des données | P0 | conforme | 11 source(s) datée(s), toutes dans leur validité ; 1 test(s) du dépôt |
| AC-050 Connaissances versionnées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-051 Mémoire intégrée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-052 Revue humaine | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-053 Gouvernance du savoir | P0 | conforme | chaque report d'achat repose sur des sources prouvées ; 2 test(s) du dépôt |
| AC-054 Visualisation | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-055 Centre de commande | P2 | sans objet | pas de centre 3D ; le panneau montre le savoir |
| AC-056 Agents de recherche | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-057 Comité de recherche | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-058 Consensus | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-059 Désaccords détectés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-060 Rapport | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-061 Sources traçables | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-062 Coûts suivis | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-063 Jetons suivis | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-064 Cache | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-065 Reprise après panne | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-066 Résultats reproductibles | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-067 Aucune source inventée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-068 Aucune affirmation sans preuve | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-069 Audit de sécurité | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-070 Objectifs de performance | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-071 Essais de chaos | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-072 Lignée des données | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-073 Expiration des connaissances | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-074 Dépendances entre sources détectées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-075 Raisonnement dans le temps | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-076 Contenu extérieur jamais exécuté | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-077 Domaine critique validé (seule Binance bloque un achat) | P0 | conforme | seules les annonces officielles de Binance bloquent un achat ; 2 test(s) du dépôt |
| AC-078 Observabilité en production | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-079 Prêt à l'exploitation | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-080 Porte finale | P0 | conforme | prouvé par 1 test(s) du dépôt |
