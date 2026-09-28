# Audit expert du code TrendGuard — 28 septembre 2026

> Depuis cet audit, le code a été rangé par rôle : les fichiers cités ici
> sont dans `trendguard/` et `research/`, et `v29.py` est devenu le paquet
> `v29/` (voir [`ARCHITECTURE.md`](ARCHITECTURE.md)).

Audit complet du dépôt (commit `022f8d5`) : architecture, sécurité, risque
financier, fiabilité en exploitation et qualité du code. Méthode : lecture du
code critique (exécution des ordres, risque, panneau, autonomie, assistant),
outils d'analyse (pyflakes, ESLint, bandit, radon, couverture des tests),
recherche de secrets dans tout l'historique git, journal du bot sur le PC,
diagnostic de la stratégie du 27 septembre (données publiques Binance) et une
variante de risque testée avec le protocole du laboratoire.

## Verdict

**Le code est sain, bien testé et prudent : aucun défaut critique trouvé.** La
stratégie est en bonne santé (diagnostic « tout est conforme »). Les vrais
risques sont ailleurs que dans le code : la clé Binance exposée dans une
conversation (à supprimer), un bot qui tourne sur un PC portable, et des
résultats historiques probablement un peu optimistes. Côté code, sept
améliorations sont recommandées (section 7). Les deux principales : tester les
chemins de secours des ordres réels, jamais exercés en paper, et limiter les
essais de mot de passe du panneau.

## 1. Le dépôt en chiffres

| Mesure | Valeur |
| --- | --- |
| Code Python (hors tests) | 17 262 lignes, 21 modules |
| Tests Python | 6 658 lignes, 376 cas, tous au vert |
| Interface web (HTML, CSS, JavaScript, gabarits) | 3 582 lignes, 15 tests navigateur (Playwright) |
| Couverture des tests (lignes exécutées) | 75 % au total (11 240 instructions) |
| Analyse statique | pyflakes : 0 ; ESLint : 0 |
| Contrôles automatiques GitHub | 3 workflows (tests et navigateur, page publique, données de la revue) |
| Secrets dans l'historique git | aucun (vérifié sur tout l'historique) |

Couverture par module (part des lignes exécutées par les tests) :

| Module | Couverture | Commentaire |
| --- | --- | --- |
| `trendguard_bot.py` (bot) | 87 % | classe du bot 91 % ; la ligne de commande (`main`) 47 % |
| `panel/` (serveur, Rachelle, actualités) | 69 à 97 % | lecture des positions en réel non testée (`data.py`) |
| `autonomy.py`, `alerts.py` | 81 %, 77 % | |
| `diagnostics.py`, `strategy_lab.py` | 88 %, 90 % | |
| `market_watch.py` | 79 % | |
| `trend_strategy.py` | 63 % | règles 100 % testées ; téléchargement et rapport de recherche non testés |
| `v29.py` (exécution) | 69 % | voir ci-dessous : chemins de secours des ordres réels |

## 2. Architecture

| Couche | Fichiers | Rôle |
| --- | --- | --- |
| Stratégie | `trend_strategy.py` | règles d'achat et de vente, backtest, sélection |
| Bot | `trendguard_bot.py` | décision quotidienne, ruse d'exécution, raisonnement |
| Exécution | `v29.py` | ordres Binance, stops, base de données, verrou |
| Veille | `market_watch.py` | annonces Binance (veto), avis des IA |
| Autonomie | `autonomy.py`, `alerts.py` | superviseur, démarrage, alertes |
| Panneau | `panel/` | serveur local, actualités, Rachelle, interface |
| Recherche | `diagnostics.py`, `strategy_lab.py`, `research_*.py` | diagnostic, études |

Points forts, rares dans des bots de ce type :

- **Le bot appelle exactement les fonctions du backtest** (`update_positions`,
  `plan_entries`) : un test vérifie qu'ils donnent les mêmes trades au centime
  près.
- **Chaque ordre est précédé de son « intention » enregistrée** : après un
  plantage au milieu d'un ordre, le bot retrouve où il en était (`reconcile`).
- **Un seul bot à la fois** (verrou du système, ordres d'une autre instance
  détectés), heure de Binance, rattrapage
  des clôtures manquées, simulateurs Binance fidèles (soldes bloqués, filtres,
  erreurs ambiguës).
- **Aucune règle ne change sans preuve** : toute variante doit battre la
  référence sur 2018-2022 ET sur 2023 → aujourd'hui.

Points faibles :

- `v29.py` fait 7 483 lignes et mélange le moteur d'exécution utilisé par
  TrendGuard avec l'ancien bot V29 (portefeuille blockchain, moteur adaptatif,
  backtest et boucle propres, environ 2 500 lignes que TrendGuard n'appelle
  pas). Maintenance plus lourde, relecture plus difficile.
- Quatre boucles de backtest voisines (`trend_strategy.backtest`,
  `research_adaptation.run_variant`, `research_selection.run`,
  `strategy_lab.simulate`) : une correction faite dans l'une peut être oubliée
  dans les autres.
- Fonctions très longues : `trendguard_bot.main` (complexité 45),
  `v29.Config.__post_init__` (46), `panel.server.PanelApp.api` (38) ;
  `panel/static/app.js` fait 1 444 lignes en un seul fichier.

## 3. Sécurité

| N° | Constat | Gravité | Recommandation |
| --- | --- | --- | --- |
| S1 | Clé API Binance collée dans une conversation (hors code) | **Critique** | La supprimer sur Binance (Gestion des API) et en créer une nouvelle avec `set-keys` |
| S2 | Mot de passe du panneau : chaque essai raté attend 1 s, mais des essais en parallèle ne sont pas limités | Moyenne (Wi-Fi seulement) | Blocage de 5 minutes après 5 échecs |
| S3 | Accès téléphone en HTTP : le mot de passe circule en clair sur le Wi-Fi | Moyenne | Wi-Fi privé seulement ; à distance, VPN (Tailscale) |
| S4 | Si une IA est configurée, les titres d'actualités (non vérifiés) entrent dans son contexte | Faible (lecture seule, aucune action possible) | Encadrer ces données comme « non fiables » dans l'instruction de Rachelle |
| S5 | Flux RSS lus avec `xml.etree` | Faible (Python 3.13 bloque les « bombes XML ») | `defusedxml` si la dépendance est acceptée |
| S6 | bandit : 56 signalements (1 « élevé », 11 « moyens ») | Aucun réel | Faux positifs vérifiés (SHA-1 anti-doublons, descriptions de variables, `0.0.0.0` volontaire) |

Déjà bien protégé : écoute locale par défaut, mot de passe obligatoire pour le
Wi-Fi, cookie `HttpOnly` et `SameSite=Strict`, contrôle d'origine et d'hôte
(CSRF, « DNS rebinding »), en-têtes CSP, fichiers servis depuis un seul dossier,
clés saisies masquées, garde-fou de Rachelle (secrets masqués avant tout envoi),
clé API sans droit de retrait vérifiée par `verify`.

## 4. Risque financier et stratégie

Diagnostic du 27 septembre (données publiques Binance, `diagnostics.py`) :
**tout est conforme**. Espérance des 79 trades des 24 derniers mois : **+1,19
R** (intervalle à 90 % : +0,37 à +2,11 R) ; rendement sur 12 mois glissants :
**+39,1 %** ; régime BTC haussier depuis 40 jours (+19,4 % au-dessus de sa
moyenne 150 jours) ; volatilité calme.

| N° | Constat | Commentaire |
| --- | --- | --- |
| R1 | Le plafond de 6 % de risque cumulé bloque aujourd'hui le 7e achat : 6 positions ouvertes à 1 % chacune, DOT signalé mais pas acheté | Voulu. Variante testée ci-dessous : compter le risque RESTANT (0 quand le stop protège le prix d'achat) au lieu du risque initial |
| R2 | Biais du survivant | Les 21 cryptos sont choisies en 2026 : les études sur données Binance ignorent les cryptos disparues. La recherche principale inclut FTT, EOS et XMR, mais pas toutes les disparues : prévoir en réel un peu moins que les chiffres historiques |
| R3 | Arrêt d'urgence à −40 % | Profond, mais le profil prudent actif divise le risque par 2 dès −10 % |
| R4 | En paper, le stop catastrophe est vérifié toutes les 60 s au dernier cours | Un creux de quelques secondes peut être manqué ; en réel, l'ordre posé chez Binance est plus fidèle |
| R5 | Mode réel jamais essayé sur le testnet de Binance | `verify`, puis quelques jours de testnet avant tout argent réel |
| R6 | Cours financiers (Yahoo Finance) : API non officielle | Affichage seulement, aucune décision n'en dépend ; repli Binance pour l'or et l'euro |

Variante R1 testée avec le protocole du laboratoire (décrite avant de regarder
le résultat) :

| Plafond de risque cumulé | 2018-22 : CAGR | Baisse max | Calmar | 2023 → : CAGR | Baisse max | Calmar |
| --- | --- | --- | --- | --- | --- | --- |
| Risque initial (actuel) | +41,2 % | −25,4 % | 1,62 | +37,2 % | −33,6 % | 1,11 |
| Risque restant | +44,1 % | −33,2 % | 1,33 | +37,9 % | −34,6 % | 1,09 |

Un peu plus de rendement, mais des baisses nettement plus fortes : moins bon sur
les deux périodes. **Le plafond actuel est le bon** ; seule l'explication
affichée sera précisée (« plafond de risque cumulé de 6 % atteint »).

## 5. Fiabilité en exploitation

Journal du bot du 26 au 28 septembre (34 heures) : aucune erreur, 29
avertissements réseau (délais dépassés vers Binance), tous rattrapés au cycle
suivant. Relance automatique vérifiée (bot tué volontairement, relancé 10 s plus
tard).

| N° | Constat | Recommandation |
| --- | --- | --- |
| O1 | Démarrage automatique à l'ouverture de session : après une coupure de courant ou une mise à jour Windows de nuit, le bot attend qu'on se connecte | Ouverture de session automatique de Windows, ou mieux : un petit serveur allumé en permanence (Docker, `docker-compose.yml` prêt) |
| O2 | Un PC portable dort ou s'éteint (capot, batterie) | Branché sur secteur ; l'anti-veille ne couvre pas le capot fermé |
| O3 | Windows : l'enregistrement du choix des cryptos peut échouer si le bot lit le fichier à la même milliseconde | Nouvel essai automatique de l'écriture |
| O4 | Sur Android, en HTTP, « Ajouter à l'écran d'accueil » crée un raccourci, pas une vraie application (HTTPS requis) | HTTPS via Tailscale si l'on veut une application plein écran |
| O5 | `docker-compose.yml` ne lance pas le panneau | Ajouter un service `panneau` |

## 6. Qualité du code

- **Tests** : 376 cas Python et 15 tests navigateur, simulateurs Binance mono et
  multi-paires, tests de sécurité du panneau et du garde-fou de Rachelle.
- **Trou de tests le plus important** : les chemins de secours des ordres RÉELS,
  jamais exécutés en paper. `ExecutionEngine.enter_planned` (l'achat réel de
  TrendGuard) n'est testé qu'à 43 % : achat au statut ambigu (arrêt de
  sécurité), ordre refusé par Binance, montant sous le minimum.
  `ExecutionEngine._modify_stop` (remontée du stop catastrophe posé chez
  Binance) n'est testé qu'à 55 % : nouveau stop refusé (l'ancien doit être
  reposé), aucun stop possible (vente d'urgence), annulation avec exécution
  partielle. Le code de ces cas est prudent à la lecture, mais il doit être
  prouvé par des tests sur le simulateur Binance avant tout passage en réel.
- **Complexité** : 37 fonctions au-dessus du seuil « difficile à maintenir »
  (radon D ou pire), dont 5 dans `trendguard_bot.py` et 14 dans `v29.py`.
- **Style** : cohérent, commentaires en français, pas d'avertissement pyflakes
  ni ESLint.
- **Dépendances** : versions figées pour Docker ; pas d'audit automatique des
  failles connues (`pip-audit`).

## 7. Plan d'action

| Priorité | Action | Qui | Suivi |
| --- | --- | --- | --- |
| 1 | Supprimer la clé Binance exposée et en créer une nouvelle | Vous | **à faire** |
| 2 | Tester les chemins de secours des ordres réels (achat ambigu ou refusé, stop refusé, vente d'urgence) sur le simulateur | Code, avant tout réel | fait : `tests/test_live_planned.py` (10 cas) ; a révélé et corrigé une erreur de mise en forme du journal à l'ouverture d'une position réelle (`v29.py`) |
| 3 | Limiter les essais de mot de passe du panneau (S2) | Code | fait : blocage de 5 min après 5 échecs en 10 min, par adresse |
| 4 | Réessayer l'écriture du choix des cryptos sous Windows (O3) | Code | fait : 10 essais espacés de 50 ms |
| 5 | Préciser « plafond de risque cumulé de 6 % atteint » dans le raisonnement (R1) | Code | fait (avec le pourcentage engagé) |
| 6 | Encadrer les actualités comme données non fiables pour l'IA de Rachelle (S4) | Code | fait |
| 7 | Audit des dépendances (`pip-audit`) dans les contrôles GitHub | Code | fait : aucune faille connue au 28/09/2026 |
| 8 | Service `panneau` dans `docker-compose.yml` (O5) | Code | reporté : dans Docker, AUTO et ARRÊTER doivent piloter un autre conteneur, relancé seul par Docker ; à concevoir avec le passage sur serveur (O1) |
| 9 | Séparer `v29.py` en modules et archiver l'ancien bot V29 ; un seul moteur de backtest paramétrable | Chantier plus long, sans urgence | fait : moteur seul dans `v29/`, ancien bot rangé dans `v29/intraday/` ; une seule boucle de backtest (`ts.backtest` et ses crochets), rapports des études identiques à l'octet près (`docs/ARCHITECTURE.md`) |
| 10 | Pour un fonctionnement 24 h/24 : serveur allumé en permanence ou ouverture de session automatique (O1) | Vous | ouvert |
| 11 | Avant le réel : `verify`, puis quelques jours de testnet (R5) | Vous, le moment venu | ouvert |

Ajouts après l'audit : anticipation de la prochaine clôture (`anticipation.py` :
ventes et achats probables, régime, risque engagé, pire cas, conseils ; carte du
tableau de bord, alertes 3 h avant la clôture, réponses de Rachelle), centre de
sécurité dans les Réglages, actualités sensibles sur une crypto détenue dans les
alertes du tableau de bord.
