# Cybersécurité et autodéfense (étape 20 du prompt maître)

Ce qui est à défendre, où le bot peut se connecter, si son code est bien celui
que vous avez fusionné, si ses bibliothèques sont celles qui ont été testées,
et la réponse prévue à chaque risque. C'est une défense : jamais offensif, rien
n'est scanné ni attaqué, aucun secret n'est lu.

Code : [`trendguard/cyber.py`](../trendguard/cyber.py), avec ce qui existait
déjà : le centre de sécurité du panneau, le rapport de sécurité de la nuit
(`report_security.py` : droits de la clé Binance, clés exposées, sauvegardes,
bibliothèques à faille connue), le moteur d'autorisation (étape 15). Tests :
[`tests/test_cyber.py`](../tests/test_cyber.py).

```text
python trendguard_bot.py cyber                       # l'état du jour
python trendguard_bot.py cyber --out docs/CYBER.md
```

Dernier état : [`CYBER.md`](CYBER.md) (jamais une adresse IP ni un nom de
réseau : seuls les libellés du rapport de la nuit sont repris, pas leurs
détails).

## Ce qui est vérifié

- **Inventaire** : code, Python et bibliothèques, base du bot, journal d'audit,
  journal financier, fichier des secrets (présence seulement), clé Binance
  (présence, et son empreinte comparée aux clés exposées connues ; jamais sa
  valeur), panneau, alertes, IA de la veille, compte Binance.
- **Sorties vers Internet** : chaque adresse écrite dans le code est comparée à
  une liste blanche (Binance, GitHub, PyPI, les IA de la veille, les canaux
  d'alerte, les sources publiques du savoir, ce PC), avec son usage. Une
  adresse hors liste est un écart ; un test voit toute adresse ajoutée.
- **Intégrité du code** : version en service (git) et fichiers suivis modifiés
  hors d'une Pull Request (il n'en faut aucun) ; la mise à jour automatique
  n'installe que ce que vous avez fusionné.
- **Bibliothèques** : versions installées contre versions testées
  (`requirements-docker.txt`) ; une faille connue est cherchée chaque nuit.
- **Événements** : points de sécurité à corriger du rapport de la nuit, ordre
  inconnu chez Binance.

## La réponse prévue

| Risque | Ce qui se fait seul | Ce qui vous revient |
| --- | --- | --- |
| clé Binance montrée ou volée | `set-keys` la refuse ; le centre de sécurité alerte | la supprimer sur Binance, en créer une sans droit de retrait |
| ordre ou position inconnus chez Binance | la paire s'arrête seule | vérifier, puis reprise explicite |
| données abîmées | aucun achat sous 50 sur 100 | rien |
| code modifié hors Pull Request | signalé chaque nuit | vérifier le dépôt |
| bibliothèque à faille connue | signalée par le rapport de la nuit | mettre à jour la version indiquée |
| mots de passe du panneau essayés | adresse bloquée 5 minutes après 5 échecs | changer le mot de passe |
| IA ou agent détourné | aucune IA n'a de droit critique ; Rachelle refuse les instructions cachées | rien |
| compromission du PC | — | `python trendguard_bot.py mode-sur on` : plus aucun achat |

Les réactions automatiques ne font que réduire. Aucune IA, aucun agent ne peut
lever l'arrêt d'urgence ou le mode sûr (moteur d'autorisation).

## L'examen : AC-001 à AC-070, note, verdict (§44-45, §49)

Mesurés : inventaire, sorties, intégrité, bibliothèques, clé exposée,
événements ; prouvés par les tests du dépôt : refus par défaut, moindre
privilège, injection d'instructions refusée, montants inventés par une IA
refusés, une seule route d'achat, audit chaîné, sauvegardes restaurées,
superviseur borné… Note pondérée : détection 15 %, identités 15 %, prévention
10 %, incidents 15 %, reprise 10 %, IA et modèles 10 %, données 10 %,
résilience 10 %, gouvernance 5 %. Un P0 raté, une adresse hors liste blanche ou
un événement P0 : NOT_READY. Le verdict **READY_FOR_PRODUCTION_SECURITY**
n'est jamais une défense autonome sans limite.

## Ce qui ne s'applique pas

- **SOC, SIEM, XDR, centre de sécurité d'entreprise** (§10-15) : un seul PC ;
  le rapport de la nuit, le centre de sécurité du panneau et cette commande en
  tiennent lieu.
- **Pare-feu et segmentation réseau** (§16) : ceux de Windows et de votre box ;
  le bot ne les change pas.
- **Double authentification, comptes d'utilisateurs** (§8) : Binance et GitHub
  l'exigent pour votre compte ; le bot n'a pas d'utilisateurs, le panneau a un
  mot de passe.
- **Riposte, contre-attaque** (§47) : interdites ; le bot ne contacte que les
  adresses de sa liste.
- **Signature d'artefacts** (§23) : aucun artefact binaire ; le code vient de
  GitHub, fusionné par vous.
