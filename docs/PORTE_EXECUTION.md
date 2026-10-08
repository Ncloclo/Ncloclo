# Porte d'exécution (étape 16 du prompt maître)

La dernière barrière avant un ordre d'achat chez Binance, en paper comme en
réel. Elle existait depuis l'étape 2 (`porte.py`) ; l'étape 16 l'a examinée
point par point et complétée là où c'était utile et sûr. Rien ne change en
paper dans le fonctionnement normal : les compléments ne font que bloquer.

```text
plan du jour → intention d'achat → contrôle (18 règles) → autorisation (5 min)
→ validation finale, juste avant l'ordre → ordre (v29) → exécution → rapprochement
```

Code : [`trendguard/porte.py`](../trendguard/porte.py) (la porte),
[`trendguard/bot_execution.py`](../trendguard/bot_execution.py) (le passage
de chaque achat), [`trendguard/porte_examen.py`](../trendguard/porte_examen.py)
(l'examen). Tests :
[`tests/test_porte_examen.py`](../tests/test_porte_examen.py),
[`tests/test_contrats.py`](../tests/test_contrats.py),
[`tests/test_live_execution.py`](../tests/test_live_execution.py),
[`tests/test_live_planned.py`](../tests/test_live_planned.py).

```text
python trendguard_bot.py porte                        # examen du jour (50 critères, chaos de 10 000 demandes)
python trendguard_bot.py porte --out docs/PORTE_EXAMEN.md
```

Dernier examen : [`PORTE_EXAMEN.md`](PORTE_EXAMEN.md).

## Ce qui a été ajouté à l'étape 16

- **Validation finale, juste avant l'ordre** (§21, P0-004) : entre le contrôle
  et l'ordre, quelque chose a-t-il changé ? L'autorisation doit être encore
  valable, liée à ce contrôle et pas encore utilisée ; l'arrêt d'urgence et le
  mode sûr (relu sur le disque, là où la commande `mode-sur on` l'écrit)
  toujours levés ; l'état critique (capital, positions et leur risque, cryptos
  permises, veto, garde du jour, moteur de risque, porte du réel, règles de
  Binance…) comparé à son empreinte du contrôle. S'il a changé, nouveau
  contrôle complet, qui doit être approuvé. Un doute ou une erreur : pas
  d'achat, tracé au journal d'audit (`porte.validation_finale`) et au rapport.
- **Règles de Binance, en réel seulement** (§11, §14-15) : paire cotée et
  active, quantité arrondie au pas du lot non nulle et dans ses bornes, montant
  d'au moins le minimum de Binance + 5 %. Ce sont les règles de l'exécution
  (v29), vérifiées une étape plus tôt, avec la raison écrite. Une règle
  illisible refuse. En paper, rien ne change.
- **Une porte en panne n'achète jamais** (P0-002, §48) : une erreur inattendue
  dans le contrôle, l'autorisation, l'empreinte ou la validation finale refuse
  l'achat et laisse continuer le reste de la décision (ventes, stops).
- **Versions** : porte `porte.v2`, registre des politiques `politiques-1.1.0`
  (nouvelle politique POL-EXCHANGE-RULES, même décision que la porte).
- **Contrats** : `FinalValidationResult.v1` (PASS seulement si chaque
  vérification tient ; un état changé est toujours recontrôlé) et
  `GateReadinessReport.v1` (examen ; jamais une autorisation du réel).

## L'essai de chaos (§52-57)

10 000 demandes d'achat tirées au hasard (graine fixe, rejouable) :
portefeuilles, risques, cryptos permises, veto, mode sûr, moteur de risque,
réel armé ou non, règles de Binance ; la même demande répétée (5 %) ; entre le
contrôle et l'ordre, arrêt d'urgence, mode sûr, risque ajouté, veto, moteur de
risque bloqué ou simple changement sans danger (20 % des demandes) ; envoi
jusqu'à 15 minutes plus tard (autorisation expirée) ; arrêt d'urgence à la
moitié. Chaque ordre envoyé est jugé par un oracle écrit sans la porte.

Attendu et obtenu : **aucun ordre non autorisé, aucun après l'arrêt d'urgence,
aucun doublon** ; une porte qui laisserait tout passer est prise par l'oracle
(test). Environ 1 ms au 95e centile pour contrôle + autorisation + validation
finale (cible 100 ms).

## L'examen : AC-001 à AC-050, note, verdict (§68-69, §73)

Chaque critère du prompt a sa priorité, sa famille et sa preuve : mesurée sur
le code (une seule route d'achat vers Binance, après la porte et la validation
finale), sur le bot (écarts entre politiques, autorisation et porte ; arrêt
d'urgence en moins de 100 ms ; journal d'audit intact), sur le chaos, ou
apportée par un test du dépôt (GitHub les rejoue à chaque envoi). Note pondérée
: sûreté 25 %, autorisation 15 %, risque 15 %, politiques 10 %, unicité des
ordres 10 %, Binance 5 %, réconciliation 10 %, traçabilité 5 %, essais 5 %.
Un P0 raté ou non mesurable : NOT_READY, quelle que soit la note.

Le verdict juge la porte, pas le réel : **READY_FOR_CONTROLLED_LIVE_EXECUTION
n'autorise rien**. Le réel reste fermé tant que la porte du réel (60 jours de
paper, 10 trades clos, sécurité sans défaut, vérification sans ordre réussie)
ne s'ouvre pas, et l'armer reste à vous seul. Ce qui ne se mesure qu'en réel
(fiabilité du vrai Binance en ordres réels, délais réels, rapprochement avec le
vrai compte) est prouvé ici sur le faux Binance des tests et sera mesuré au
réel contrôlé (étape 17).

## Exigences de l'étape 16 → TrendGuard

| Exigence | Dans TrendGuard |
| --- | --- |
| Aucun ordre sans la porte, aucune autre route (§2, P0-003) | une seule route d'achat dans le code (`_execute_entry`), après la porte et la validation finale ; vérifié par l'examen et par un test qui voit une route de plus |
| Refus par défaut, fail-closed (P0-001, P0-002, §48) | refus sans raison écrite impossible ; porte en panne, règle illisible, mesure absente : pas d'achat |
| Validation de l'autorisation (§6) | liée à son contrôle, 5 minutes, à usage unique (clé d'unicité), vérifiée de nouveau juste avant l'ordre |
| Politiques et risque revérifiés (§7-8, §54) | validation finale : état changé → nouveau contrôle complet (politiques, risque, moteur de risque) |
| Compte, portefeuille (§9-10) | arrêt d'urgence, mode sûr, argent disponible (comptant), positions, risque cumulé, taille |
| Instrument, quantité, prix, notionnel (§11, §13-15) | règles de Binance en réel (pas du lot, bornes, minimum), stop sous le prix, montant minimum, taille maximale |
| Levier, marge (§16) | Spot au comptant : ni levier ni marge (contrôle « Argent disponible ») |
| Liquidité (§17) | volume minimum avant tout achat ; carnet anormal ou trop mince : achat différé |
| Doublons, identifiant client (§19-20, §57) | clé d'unicité par crypto et par jour, identifiant client unique par ordre (v29), un identifiant déjà ouvert refusé |
| TOCTOU (§21) | empreinte au contrôle, validation finale avant l'ordre |
| Exactement une exécution, état inconnu, pas de nouvel envoi aveugle (§29-30, §33, §53) | v29 : intention persistée avant l'envoi, réponse perdue → recherche par identifiant client, introuvable → arrêt et adoption après vérification ; horloge refusée → même identifiant |
| Cycle de vie, exécutions partielles (§31-32) | v29 : FLAT → OPENING → OPEN → CLOSING, HALTED ; exécution partielle comptée sur la quantité exécutée |
| Disjoncteurs, arrêt d'urgence (§35-37) | arrêt d'urgence (baisse maximale), arrêt du jour, mode sûr ; erreurs de Binance sans arrêt inutile ; ventes toujours possibles |
| Réconciliation (§38-40) | v29 au démarrage et à chaque cycle : ordres et soldes de Binance font foi ; ordre inconnu : arrêt |
| Audit, incidents, alertes (§41, §45, §50, §64) | journal d'audit chaîné (contrôle, autorisation, blocage final, ordre) ; arrêts et écarts au journal du bot, au rapport et en alerte |
| Sécurité, horloge (§46-47) | secrets jamais écrits dans les journaux ; horloge de Binance, resynchronisée |
| IA et agents (§62) | aucune route vers Binance ; aucun droit critique (moteur d'autorisation) |
| Tests de chaos et critiques (§51-57) | chaos de 10 000 demandes, révocation et changement de risque ou de politique avant l'ordre, panne de chaque composant, réponses perdues, exécutions en double |
| Critères, note, verdict (§68-69, §73) | `porte_examen.py`, contrat `GateReadinessReport.v1`, [`PORTE_EXAMEN.md`](PORTE_EXAMEN.md) |

## Ce qui ne s'applique pas

- **Plusieurs courtiers, routage, registre de courtiers** (§23-24) : un seul,
  Binance Spot ; l'adaptateur est celui de v29.
- **Signature de l'ordre par une clé matérielle (KMS, HSM)** (§27) :
  l'ordre ne quitte le bot que signé par la clé API de Binance ; l'autorisation
  ne quitte jamais le processus du bot.
- **API de la porte, file de messages** (§44-45) : la porte est une fonction
  appelée par le bot, pas un service réseau ; aucune API ne permet d'acheter.
- **Types d'ordres avancés** (§26) : achat au marché seulement (moins de 0,03 %
  du volume du jour) ; stops gérés par v29 (OCO, stop seul).
- **Marge, levier, marché fermé** (§12, §16) : Spot au comptant, marché ouvert
  24 heures sur 24 ; une maintenance de Binance rend le cours indisponible et
  l'achat est différé.
- **Bande de prix** (§13) : l'achat au marché est recalculé au prix du moment
  pour ne jamais risquer plus que prévu, et annulé si le prix est retombé près
  du stop ; une bande de plus n'ajouterait rien.
