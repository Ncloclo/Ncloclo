# Les bourses du monde

La structure de cotation de chaque grande place, comme la BRVM pour
l'UEMOA : ses heures de séance, ses indices phares, son régulateur, l'accès
depuis la Côte d'Ivoire et ce que le bot en lit. Module :
`trendguard/bourses.py` ; page « Bourses du monde » du panneau (menu
Marchés) ; commande `python trendguard_bot.py bourses`.

## Les places suivies

28 places, rangées par région :

- Afrique : BRVM (Abidjan, huit pays de l'UEMOA), BVMAC (Douala, six pays de
  la CEMAC), JSE (Johannesburg), NGX (Lagos), Bourse de Casablanca, EGX (Le
  Caire), NSE (Nairobi), GSE (Accra).
- Amériques : NYSE et Nasdaq (New York), Cboe (Chicago : options, VIX), TSX
  (Toronto), B3 (São Paulo), BMV (Mexico).
- Europe : Euronext (Paris, Amsterdam, Bruxelles, Lisbonne, Dublin, Milan,
  Oslo), London Stock Exchange, Deutsche Börse (Xetra), SIX (Zurich), Bolsa
  de Madrid.
- Asie et Moyen-Orient : Tokyo, Shanghai, Shenzhen, Hong Kong, NSE et BSE
  (Bombay), KRX (Corée du Sud), Saudi Exchange (Tadawul).
- Océanie : ASX (Sydney).

Pour chacune : ville, monnaie, heures de séance (heure locale et heure
d'Abidjan, en séance ou non en ce moment), indices phares, régulateur, accès
depuis la Côte d'Ivoire, site officiel. Les jours fériés ne sont pas connus
du bot. Les heures dont les sources ne concordent pas sont marquées
« indicatives » (B3, NSE de Nairobi) ; celles de la BVMAC et du Ghana ne sont
pas données.

## Ce que le bot lit

- Les indices de chaque place, chaque jour chez Yahoo Finance, avec la
  tendance de la règle du bot (le suivi quotidien des marchés). Quatre indices
  ajoutés le 25/11/2026 : NYSE Composite, SZSE Component, Sensex, FTSE/JSE
  Top 40. Le TASI saoudien n'a pas d'historique journalier chez Yahoo : la
  place est suivie par le fonds MSCI Arabie saoudite (KSA).
- Comme la BRVM, les pages officielles de quatre bourses africaines, une fois
  par séance après 17:00 UTC, sous le nom du bot, là où leur robots.txt le
  permet :
  - NGX (Nigeria) : cours de chaque action et indice All-Share, par les deux
    adresses JSON publiques de son propre site. Pendant la séance, la liste
    du jour n'a pas encore de cours de clôture : le bot garde ceux de la
    veille et relit trois heures plus tard ;
  - NSE (Nairobi) : indices (NASI, NSE 20, NSE 25, NSE 10, banques) et
    activité du marché ;
  - Bourse de Casablanca : MASI, MASI 20, plus fortes hausses et baisses de
    sa page d'accueil ;
  - BVMAC (Douala) : le bulletin officiel de la cote (PDF d'environ 3 Mo,
    lu avec la bibliothèque libre pypdf) : indice BVMAC All Share et cours de
    chaque action.
- Le bot construit l'historique de ces pages séance après séance
  (`data_bourses/`), sans compter les jours fériés (page inchangée).
- Pas lues : l'EGX (site injoignable depuis ce PC, indice sans historique
  chez Yahoo), la GSE (pas de page de cours à jour lisible simplement), le
  TOPIX et le CSI 300 (sans historique chez Yahoo).

## La règle du bot sur chaque place

Même protocole que l'étude des pays (`docs/PAYS.md`) : la règle du bot,
paramètres gelés et coût réel des indices, sur l'indice principal de chaque
place, face à l'achat-conservation ; elle « tient » si son Calmar est
meilleur sur 2018-2022 ET depuis 2023. Recalculée après chaque lecture des
marchés.

Lecture du 25/11/2026 (séances jusqu'au 24/11/2026), Calmar de la règle face
à celui de l'achat-conservation :

| Place | Indice | 2018-2022 | Depuis 2023 | La règle tient ? |
| --- | --- | --- | --- | --- |
| JSE | FTSE/JSE Top 40 | −0,04 contre 0,13 | −0,18 contre 0,50 | non |
| NYSE | NYSE Composite | −0,14 contre 0,09 | 0,07 contre 0,77 | non |
| Nasdaq | Nasdaq Composite | 0,56 contre 0,23 | 0,73 contre 0,97 | non |
| TSX | S&P/TSX Composite | 0,24 contre 0,09 | 0,31 contre 1,20 | non |
| B3 | Ibovespa | 0,10 contre 0,15 | 0,34 contre 0,55 | non |
| BMV | S&P/BMV IPC | −0,03 contre −0,02 | 0,21 contre 0,55 | non |
| Euronext | CAC 40 | −0,06 contre 0,11 | −0,11 contre 0,31 | non |
| London Stock Exchange | FTSE 100 | −0,14 contre −0,01 | 0,06 contre 0,65 | non |
| Deutsche Börse | DAX 40 | −0,16 contre 0,04 | 0,00 contre 1,00 | non |
| SIX Swiss Exchange | SMI | −0,06 contre 0,09 | −0,02 contre 0,45 | non |
| Bolsa de Madrid | IBEX 35 | −0,19 contre −0,09 | 0,04 contre 1,86 | non |
| Bourse de Tokyo | Nikkei 225 | −0,04 contre 0,07 | 0,52 contre 0,86 | non |
| Bourse de Shanghai | SSE Composite | 0,07 contre −0,05 | 0,05 contre 0,16 | non |
| Bourse de Shenzhen | SZSE Component | 0,20 contre −0,01 | −0,05 contre −0,04 | non |
| HKEX | Hang Seng | −0,06 contre −0,15 | 0,10 contre 0,08 | oui, de justesse |
| NSE | Nifty 50 | 0,36 contre 0,30 | 0,11 contre 0,56 | non |
| BSE | Sensex | 0,37 contre 0,33 | 0,03 contre 0,48 | non |
| KRX | KOSPI | −0,04 contre −0,05 | 0,59 contre 0,44 | oui, de justesse |
| Saudi Exchange | Fonds MSCI Arabie saoudite (KSA, en $) | 0,84 contre 0,19 | −0,21 contre −0,10 | non |
| ASX | S&P/ASX 200 | −0,15 contre 0,08 | −0,22 contre 0,43 | non |

La règle ne bat l'achat-conservation que sur 2 places sur 20 (Hong Kong et
la Corée du Sud), de justesse : leurs Calmar sont presque égaux. Sur un
indice d'actions seul, elle ne garde qu'une petite partie du capital
investie : ses baisses sont faibles, mais ses gains aussi. Comme pour les
pays, elle reste réservée aux cryptos ; ces bourses servent au savoir du
bot, pas à ses décisions.

Information seulement : aucune décision du bot n'en dépend.

## Investir depuis la Côte d'Ivoire

D'après le texte fourni par l'utilisateur le 25/11/2026, corrigé là où
c'était possible ; à vérifier avant d'agir :

- BRVM : par une SGI agréée par l'AMF-UMOA, en FCFA.
- Autres bourses : par un courtier international régulé (SEC et FINRA aux
  États-Unis, FCA, CySEC…) qui accepte les résidents ivoiriens ; les SGI n'y
  donnent pas accès. Méfiance envers les courtiers non régulés et les offres
  limitées aux CFD.
- Courtiers cités (Interactive Brokers, XTB, TradeStation Global, Charles
  Schwab) : accueil des résidents ivoiriens, frais et dépôt minimum à
  vérifier.
- Ouverture du compte : passeport, justificatif de domicile de moins de
  3 mois, formulaire W-8BEN pour les actions américaines (non-résident fiscal
  des États-Unis).
- Envoi des fonds : virement international en dollars ou en euros ;
  réglementation des changes de la BCEAO (origine des fonds, motif) ; frais
  fixes par virement.
- Impôts : pas d'impôt américain en général sur les plus-values d'actions
  des non-résidents ; dividendes retenus à 30 % à la source, faute de
  convention fiscale entre les deux pays ; gains à déclarer en Côte d'Ivoire.

Le bot n'ouvre aucun compte et ne passe aucun ordre sur ces bourses.

## Commandes

```text
python trendguard_bot.py bourses            # chaque place, sa séance, ses indices
python trendguard_bot.py bourses lire       # pages officielles relues tout de suite
```
