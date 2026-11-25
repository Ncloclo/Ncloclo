# Paiement : les moyens de payer près de chez vous

La page « Paiement » du panneau, sous « Réglages » dans le menu, montre les
moyens de payer, d'être payé et d'envoyer de l'argent dans le pays de
l'appareil qui la regarde, et dans sa zone. Module :
`trendguard/paiements.py` ; commande : `python trendguard_bot.py paiements
[pays]`.

## Ce qu'elle montre

- **Paiement mobile** : les porte-monnaie des opérateurs (Orange Money, MTN
  MoMo, Moov Money, Wave, M-Pesa, Airtel Money…) et les applications d'envoi
  d'argent (UPI, Pix, Zelle, Bizum…).
- **Cartes bancaires** : de débit ou prépayées, Visa et Mastercard partout,
  et les réseaux de la zone ou du pays (GIM-UEMOA, GIMAC, Verve, RuPay, CB…).
- **Paiement en ligne et passerelles** : PayPal, Apple Pay et Google Pay,
  et les passerelles locales des sites marchands (CinetPay, PayDunya,
  Paystack, Flutterwave, Hubtel, CMI…).
- **Banque et virements** : virement international (SWIFT), virements de la
  zone (UEMOA, CEMAC, SEPA) et virements instantanés du pays.
- **Envoi d'argent** : Western Union, MoneyGram, et les services qui envoient
  depuis l'étranger vers un porte-monnaie mobile.
- **Vers les USDT du bot et retour** : Binance P2P (avec ses précautions) et
  l'achat par carte chez Binance.
- **Payer en sécurité** : quatre conseils, dans la langue du panneau.

Chaque moyen dit s'il vient du pays, de la zone ou de partout. Une soixantaine
de pays ont leurs moyens propres (toute l'Afrique de l'Ouest et du Centre,
Nigeria, Ghana, Kenya, Afrique du Sud, Maghreb, Égypte, grands pays d'Europe,
d'Amérique et d'Asie) ; les autres ont ceux de leur zone et de partout.

## Comment le pays est trouvé

Tout se décide sur l'appareil, sans service extérieur :

1. le pays choisi sur la page, ou trouvé par la position, s'il y en a un
   (gardé sur cet appareil) ;
2. sinon le fuseau horaire de l'appareil (base des fuseaux, sur le PC) ;
   quand un fuseau en vaut un autre (Douala et Lagos, Dakar et Abidjan), la
   région de la langue de l'appareil départage (fr-CM, fr-SN…) ;
3. sinon la région de la langue de l'appareil, sinon le pays du profil du
   bot (`TG_PAYS`).

« Me localiser » demande la position au navigateur (vous l'autorisez ou
non). La ville la plus proche est cherchée sur le PC du bot, dans la table
des villes de plus de 15 000 habitants de GeoNames (licence CC BY 4.0,
`trendguard/villes.tsv.gz`, refaite par `python -m research.villes`). La
position est arrondie au kilomètre, n'est jamais gardée et ne part vers
aucun service extérieur ; seuls le pays et la ville trouvés restent sur
l'appareil. Une fois l'autorisation donnée, la page retrouve votre ville
seule à chaque visite. Le panneau n'ouvre la position qu'à lui-même
(`Permissions-Policy: geolocation=(self)`) ; caméra, micro et paiement
restent fermés.

## Ce que le bot ne fait pas

Le bot ne reçoit, n'envoie ni ne garde aucun paiement. Il ne demande jamais
un numéro de carte, un code secret, un mot de passe ou un code reçu par SMS.
Aucun lien n'est donné vers un service de paiement : on y va par son
application officielle. Noms d'opérateurs et de services connus ; conditions,
frais et disponibilité changent : à vérifier auprès de chacun.

## Ajouter un pays ou un moyen

Dans `trendguard/paiements.py`, `COUNTRIES` : une entrée par pays (code ISO),
chaque moyen avec sa famille, son nom, ce qu'il permet en français et en
anglais, et une précaution si besoin. Les tests (`tests/test_paiements.py`)
vérifient que chaque moyen est complet dans les deux langues.
