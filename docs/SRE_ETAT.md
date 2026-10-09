# Fiabilité et autoréparation encadrée

Mesuré le 2026-10-09 par `python trendguard_bot.py sre` (étape 32 du prompt
maître, [`SRE.md`](SRE.md)). Une prévision n'est pas une certitude ; une cause
soupçonnée n'est pas prouvée.

- **READY** ; note 100/100.
- 1 anomalie(s) ; 11 arrêt(s) imprévu(s) sur 7 jours (21,1 h) ; 1 remédiation(s)
  proposée(s), 0 automatique(s) ; prévision : arrêt imprévu vers 6 h UTC
  (probabilité 86 %).

## Anomalies

| Mesure | référence | vu | gravité | preuve |
| --- | --- | --- | --- | --- |
| arrêts imprévus sur 7 jours | 4,0 par semaine avant | 11 | HIGH | 21,1 h d'arrêt cette semaine |

## Prévisions

- arrêt imprévu vers 6 h UTC : probabilité 86 % ; horizon environ 1 jour(s) ; 12
  jour(s) avec un arrêt sur 14 ; 5 arrêt(s) commencé(s) vers cette heure.

## Capacité

- Disque : 14,3 Go disponibles (total 240 Go) ; marge avant l'alerte : 9,3 Go.
- Base du bot : 0,6 Mo.

## Remédiations proposées

| Service | état | niveau | action | automatique | qui l'autorise | rayon d'impact |
| --- | --- | --- | --- | --- | --- | --- |
| alertes | DEGRADED | MEDIUM | corriger le canal d'alerte (python trendguard_bot.py alerts configurer) | non | vous | MEDIUM (aucun autre) |

## Qualité

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| intelligence SRE (anomalies mesurées) | 15 % | conforme | 1 anomalie(s) mesurée(s), chacune avec sa référence |
| incidents | 10 % | conforme | 1 service(s) en panne ou dégradé(s), chacun avec sa procédure |
| causes racines | 10 % | conforme | cause soupçonnée tant qu'elle n'est pas prouvée |
| prévisions | 10 % | conforme | 1 prévision(s), jamais présentées comme certaines ; 1 jour(s) mesuré(s) |
| résilience | 10 % | conforme | graphe des services sans cycle ; points uniques : pc, base, internet, binance |
| autoréparation encadrée | 10 % | conforme | seules les remédiations LOW se font seules ; une CRITICAL attend vous |
| sécurité (aucune autorisation propre) | 15 % | conforme | aucune autorisation créée, aucune écriture hors de son historique |
| observabilité | 5 % | conforme | chaque remédiation dit son symptôme, son rayon d'impact et sa procédure |
| reprise et retour arrière | 5 % | conforme | 11 sauvegarde(s), la dernière il y a 11 h |
| performance | 5 % | conforme | en 0,41 s |
| gouvernance | 5 % | conforme | 18 services avec leur niveau de remédiation et qui l'autorise |
