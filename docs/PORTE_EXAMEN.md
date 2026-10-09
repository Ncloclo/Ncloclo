# Examen de la porte d'exécution (critères AC-001 à AC-050)

Mesuré le 2026-10-08 par `python trendguard_bot.py porte` (étape 16 du prompt
maître, [`PORTE_EXECUTION.md`](PORTE_EXECUTION.md)). L'examen juge la porte, pas
le réel : il n'autorise rien. Le réel reste fermé tant que la porte du réel ne
s'ouvre pas, et l'armer reste à vous seul.

## Verdict

- **PRÊTE pour un réel contrôlé** (READY_FOR_CONTROLLED_LIVE_EXECUTION).
- Note de préparation 100,0/100 (prête) ; critères P0 non satisfaits : 0.
- Chaos : 10 000 demandes d'achat, 131 ordre(s) envoyé(s), 0 non autorisé(s), 0
  après l'arrêt d'urgence, 0 doublon ; 1,00 ms au 95e centile.
- Bot : aucun achat contrôlé dans l'état lu.
- Mesurable seulement en réel : la fiabilité du vrai Binance en ordres réels,
  les délais réels d'accusé de réception et d'exécution, le rapprochement avec
  le vrai compte. Ici ils sont prouvés sur le faux Binance des tests (pannes,
  réponses perdues, exécutions partielles ou en double) ; ils seront mesurés au
  réel contrôlé (étape 17).

## Chaos

| Mesure | valeur |
| --- | --- |
| demandes | 10 000 |
| intentions invalides (contrat) | 0 |
| demandes répétées | 510 |
| contrôles approuvés | 205 |
| changements entre contrôle et ordre | 2 013 |
| envois après expiration | 2 472 |
| approuvés puis arrêtés à la validation finale | 74 |
| envoyés après un nouveau contrôle | 9 |
| ordres envoyés | 131 |
| ordres non autorisés (oracle) | 0 |
| ordres après l'arrêt d'urgence | 0 |
| doublons envoyés | 0 |

## Familles

| Famille | poids | note |
| --- | --- | --- |
| sûreté | 25 % | 100 |
| intégrité de l'autorisation | 15 % | 100 |
| contrôle du risque | 15 % | 100 |
| contrôle des politiques | 10 % | 100 |
| unicité des ordres | 10 % | 100 |
| fiabilité de Binance | 5 % | 100 |
| réconciliation | 10 % | 100 |
| traçabilité | 5 % | 100 |
| essais | 5 % | 100 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Aucun ordre ne contourne la porte | P0 | conforme | un seul appel d'achat vers Binance, dans l'exécution d'un achat, après la porte et la validation finale ; 2 test(s) du dépôt |
| AC-002 Aucun ordre non autorisé n'atteint Binance | P0 | conforme | chaos : 0 ordre non autorisé sur 131 envoyé(s) ; 3 test(s) du dépôt |
| AC-003 Autorisation expirée toujours rejetée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-004 Autorisation révoquée toujours rejetée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-005 Politique non tenue : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-006 Violation du risque : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-007 Compte gelé : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-008 Marché fermé ou en maintenance : ordre bloqué | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-009 Instrument non négociable : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-010 Quantité invalide : ordre bloqué | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-011 Prix invalide : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-012 Notionnel au-dessus de la limite : ordre bloqué | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-013 Marge insuffisante : ordre bloqué | P0 | sans objet | Binance Spot sans marge ni emprunt : un achat se paie comptant (contrôle « Argent disponible ») |
| AC-014 Levier au-dessus de la limite : ordre bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-015 Liquidité insuffisante : achat bloqué ou différé | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-016 Doublon empêché | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-017 Identifiant d'ordre client unique | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-018 Délai dépassé à l'envoi : jamais de nouvel envoi à l'aveugle | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-019 État inconnu : réconciliation | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-020 Chaque ordre a sa trace | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-021 Chaque ordre a son identifiant d'exécution | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-022 Chaque ordre a son identifiant client | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 Chaque ordre est lié à son autorisation | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-024 Chaque exécution est réconciliée | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-025 Tout écart critique crée un incident | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-026 L'arrêt d'urgence bloque tout nouvel ordre | P0 | conforme | achat bloqué en 0,15 ms ; chaos : 0 ordre après l'arrêt d'urgence (demande n° 5 001 sur 10 000) ; 2 test(s) du dépôt |
| AC-027 Disjoncteurs | P1 | conforme | prouvé par 3 test(s) du dépôt |
| AC-028 Aucun secret dans les journaux | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-029 Une IA ne peut pas envoyer d'ordre | P0 | conforme | aucune route d'achat hors du bot ; Rachelle, les IA et les agents n'en ont aucune ; 2 test(s) du dépôt |
| AC-030 Un agent ne peut pas envoyer d'ordre | P0 | conforme | aucune route d'achat hors du bot ; Rachelle, les IA et les agents n'en ont aucune ; 2 test(s) du dépôt |
| AC-031 Seul le service d'exécution (le bot) envoie | P0 | conforme | aucune route d'achat hors du bot ; Rachelle, les IA et les agents n'en ont aucune ; 2 test(s) du dépôt |
| AC-032 Panne du risque : aucun achat | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-033 Panne des politiques : aucun achat | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-034 Panne de l'autorisation : aucun achat | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-035 Panne de l'unicité ou de l'audit : aucun achat | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-036 Panne critique : mode sûr | P0 | conforme | mode sûr prêt ; fichier illisible : actif ; évaluation du risque absente : aucun achat ; 2 test(s) du dépôt |
| AC-037 Toutes les décisions sont traçables | P0 | conforme | intact : 3 événement(s), dernier le 2026-10-08 à 17:20 ; 1 test(s) du dépôt |
| AC-038 Tous les envois sont traçables | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-039 Toute erreur critique déclenche une alerte | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Le chaos ne produit aucun ordre non autorisé | P0 | conforme | 10 000 demandes, 131 ordre(s) envoyé(s), 0 non autorisé(s) selon l'oracle ; 2013 changement(s) entre contrôle et ordre, 2472 envoi(s) tardif(s) ; 2 test(s) du dépôt |
| AC-041 Résiste aux doublons | P0 | conforme | 510 demande(s) répétée(s) : 0 doublon envoyé ; 2 test(s) du dépôt |
| AC-042 Résiste aux réponses perdues | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-043 Résiste aux exécutions en double | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-044 Résiste aux messages dans le désordre | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-045 Résiste à un changement de risque pendant l'exécution | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-046 Résiste à une révocation pendant l'exécution | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-047 Résiste à un changement de politique pendant l'exécution | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-048 Mode d'urgence | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-049 Réconciliation | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-050 Aucune performance au prix de la sécurité | P1 | conforme | contrôle + autorisation + validation finale : 1,00 ms au 95e centile (cible 100) ; contrôle seul 0,15 ms (cible 50) ; aucun raccourci : chaque achat passe tout ; 1 test(s) du dépôt |
