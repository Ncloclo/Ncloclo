# Moteur de politiques (étape 14 du prompt maître)

Les règles qui disent quand un achat est permis, limité, soumis à votre accord,
reporté ou interdit, écrites en registre : chacune a un identifiant, une
version, un type, une gravité, une catégorie, la condition qui doit tenir et
l'action quand elle ne tient pas. Le registre est évalué à chaque achat ; la
porte d'exécution, elle, reste la seule à appliquer.

```text
intention d'achat + portefeuille + réglages → contexte (liste blanche de champs)
→ chaque politique active : sa condition tient-elle ? (illisible = blocage)
→ conflits : l'action la plus grave l'emporte
   compte gelé > mode sûr > interdit > votre accord d'abord > pas d'achat aujourd'hui
   > taille réduite > permis dans les limites > permis
→ décision expliquée (politique, version, valeur observée, seuil), valable 5 minutes
→ comparée à la décision de la porte d'exécution : un écart est signalé
```

Code : [`trendguard/politique.py`](../trendguard/politique.py).
Tests : [`tests/test_politique.py`](../tests/test_politique.py).

```text
python trendguard_bot.py politique                # le registre, et sa cohérence
python trendguard_bot.py politique simulation     # les politiques de risque éprouvées sur l'historique
```

## Le registre

Dix-neuf politiques (version `politiques-1.0.0`). Dix-sept sont bloquantes et
correspondent chacune à un contrôle de la porte d'exécution : mode réel armé
par vous (sinon : votre accord d'abord), porte du réel, arrêt d'urgence (sinon
: compte gelé), mode sûr, garde « pas de trade » du jour (sinon : pas d'achat
aujourd'hui), décision du jour, crypto autorisée, doublon, nombre de positions,
risque de l'achat, risque cumulé, taille, argent disponible (ni levier ni
emprunt), stop sous le prix, montant minimum, qualité des données, évaluation du
moteur de risque (sinon : mode sûr). Deux informent sans bloquer : le profil
prudent (taille réduite après une baisse de 10 %) et une grande annonce dans les
48 heures (prudence, effet non prouvé).

## Ce qui est vérifié

- **Même décision que la porte** : 3 000 cas tirés au hasard (tous les champs
  du contexte), 0 écart ; à chaque achat du bot, la décision du registre est
  comparée à celle de la porte ; un écart est écrit au journal du bot et au
  rapport, jamais appliqué.
- **Bornes** : chaque seuil éprouvé à seuil − ε, seuil, seuil + ε.
- **Sécurité par défaut** : une politique illisible (champ ou opérateur
  inconnu) bloque, jamais ne permet ; un registre incohérent (identifiant en
  double, contrôle de la porte sans politique) est signalé.
- **Aucune dérogation silencieuse** : une politique change dans le code, avec
  une nouvelle version et ses tests ; une décision n'est jamais une
  autorisation (le contrat le refuse).

## Les politiques de risque, éprouvées

Que se serait-il passé avec d'autres politiques de risque (même règle, frais et
glissement compris) ? `python trendguard_bot.py politique simulation` :

| Politique | 2018-2022 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar |
| --- | --- | --- | --- | --- | --- | --- |
| en vigueur (20 positions, 10 %, profil prudent) | +44,0 % | −32,3 % | 1,36 | +33,2 % | −25,2 % | 1,32 |
| recherche (8 positions, 6 %) | +31,8 % | −20,6 % | 1,54 | +29,8 % | −24,2 % | 1,23 |
| sans profil prudent | +46,1 % | −36,8 % | 1,25 | +35,0 % | −35,5 % | 0,98 |
| arrêt d'urgence à −30 % | +44,9 % | −30,1 % | 1,49 | +33,2 % | −25,2 % | 1,32 |

Le profil prudent protège nettement depuis 2023 (baisse −25 % au lieu de −36 %).
Les réglages de la recherche baissent moins en 2018-2022 mais rapportent moins ;
les vôtres sont votre choix, et un arrêt d'urgence plus serré se discute :
aucune politique ne change sans votre décision.

## Exigences de l'étape 14 → TrendGuard

| Exigence de l'étape 14 | Dans TrendGuard |
| --- | --- |
| Contexte, registre, versions, types (§7-9, §48) | contexte en liste blanche ; registre versionné : identifiant, version, type, gravité, catégorie, portée, condition, action, raison, état |
| Langage déclaratif (§10) | conditions en données (tout, l'un, non, comparaison à une valeur ou à un autre champ × facteur + marge) ; aucun code exécuté |
| Actions (§11) | permis, permis dans les limites, taille réduite, pas d'achat aujourd'hui, votre accord d'abord, interdit, mode sûr, compte gelé |
| Éligibilité, instruments, données, modèle, événements (§12-20) | crypto de la liste, choisie, sans veto ; données du jour, qualité ; moteur de risque ; annonces : information seulement |
| Baisse, pertes, levier, marge, liquidité, concentration (§21-29) | arrêt d'urgence, profil prudent, risque de l'achat et cumulé, taille par position, nombre de positions, ni levier ni marge (Spot), montant minimum |
| Autonomie, accord humain (§32-33) | réel armé par vous et porte du réel ; sinon « votre accord d'abord » ou interdit |
| Conflits, décision, expiration, explication (§34-41) | la plus grave l'emporte ; décision expliquée (version, observé, seuil) valable 5 minutes |
| Simulation, backtest des politiques (§38-40) | politiques de risque rejouées sur les deux époques |
| Sécurité par défaut, interdictions (§4, §60, §65) | politique illisible = blocage ; la porte reste le seul point d'application ; ni ordre ni autorisation |
| Tests (§49-51) | bornes, conflits, illisible, 3 000 cas contre la porte, bot inchangé |

## Ce qui ne s'applique pas

- **Dérogations** (§43-44) : il n'y en a pas ; une règle change dans le code,
  versionnée et testée.
- **Comité de politiques, agents de politique** (§45-46) : pas d'IA (aucune
  clé) ; le registre et ses tests en tiennent lieu.
- **Juridictions, marges, sessions de marché, horaires** (§14-15, §24) :
  cryptos au comptant, cotées sans interruption.
- **API, Redis, bus d'événements** (§53-56) : un seul PC ; commande, journal du
  bot et rapport.
