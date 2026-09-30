# Documentation TrendGuard — sommaire

Tous les documents du dossier `docs/`, rangés par question. Le mode d'emploi
du bot (installation, commandes, panneau) est dans le
[`README.md`](../README.md) à la racine.

## Comprendre le bot

| Document | Ce qu'on y trouve |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | comment le code est rangé, qui dépend de qui, règles communes |
| [`APPRENTISSAGE.md`](APPRENTISSAGE.md) | ce que le bot apprend librement (carnets, ruse, prévisions) |
| [`EVOLUTION.md`](EVOLUTION.md) | les réglages que le bot peut changer seul, sous épreuves |
| [`RAPPORT.md`](RAPPORT.md) | le rapport quotidien de 00:30 UTC : sécurité, diagnostic, envoi |

## Les preuves : études reproductibles

| Document | Question posée |
| --- | --- |
| [`TRENDGUARD_REPORT.md`](TRENDGUARD_REPORT.md) | la stratégie est-elle rentable hors échantillon ? |
| [`ROBUSTESSE.md`](ROBUSTESSE.md) | les résultats tiennent-ils quand tout se dégrade ? |
| [`EXAMEN.md`](EXAMEN.md) | le bot est-il intelligent et rusé ? |
| [`SELECTION.md`](SELECTION.md) | quelles cryptos trader, et quand vendre ? |
| [`ADAPTATION.md`](ADAPTATION.md) | le bot peut-il « apprendre et s'adapter » ? |
| [`STRATEGIES.md`](STRATEGIES.md) | le bot peut-il « apprendre la meilleure stratégie » ? |

Les cinq dernières indiquent en tête la commande qui les régénère ; le rapport
de recherche se régénère avec `python trendguard_bot.py strategy research --data
data`. Ce sont des diagnostics : aucune étude ne change une règle du bot.

## Contrôles et suivi

| Document | Ce qu'on y trouve |
| --- | --- |
| [`AUDIT.md`](AUDIT.md) | audit et diagnostic expert du 28 au 30 septembre 2026 : état, constats, plan d'action |
| [`DIAGNOSTIC_CODE.md`](DIAGNOSTIC_CODE.md) | analyse du code du 30 septembre 2026 : mesures, harmonisation, structure |
| [`revues/`](revues/) | revues hebdomadaires écrites par la routine du lundi |
