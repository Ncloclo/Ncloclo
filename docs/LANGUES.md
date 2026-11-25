# Langues : le panneau parle la langue officielle de votre pays

Le panneau et Rachelle prennent la langue officielle du pays de l'appareil
qui les regarde : français dans un pays francophone, anglais dans un pays
anglophone, allemand, espagnol, portugais, russe, chinois, arabe… Modules :
`trendguard/profil.py` (langues officielles), `panel/static/js/i18n.js`
(traduction), `panel/static/js/geo.js` (pays de l'appareil),
`panel/rachelle_en.py` (Rachelle en anglais).

## Quelle langue

1. Le pays de l'appareil est trouvé comme pour la page Paiement
   ([`PAIEMENTS.md`](PAIEMENTS.md)) : fuseau horaire, langue de l'appareil,
   position si vous l'autorisez.
2. Ses langues officielles viennent de CLDR, la base d'Unicode (249 pays,
   119 langues, figées dans `profil.py`, sans réseau). Dans un pays à
   plusieurs langues (Cameroun, Canada, Suisse, Belgique…), celle de
   l'appareil passe d'abord.
3. Le panneau prend la première qu'il sait afficher ; sinon l'anglais.

Un autre choix reste possible : Réglages ▸ Affichage, ou la page Paiement.
« Automatique » revient à la langue du pays.

## Comment chaque langue s'affiche

| Langue | Comment |
| --- | --- |
| français | langue d'origine de chaque texte |
| anglais | dictionnaire complet (`i18n/en.js`, plus de 1 100 textes) |
| allemand, espagnol, portugais, russe, chinois, arabe | dictionnaire de l'essentiel (menu, titres, états, réglages, Paiement, Rachelle) ; le reste par la traduction de l'appareil, sinon en anglais |
| toute autre langue officielle (italien, japonais, turc, hindi…) | traduction de l'appareil, à partir de l'anglais ; sinon l'anglais |

La traduction de l'appareil est celle que Chrome sur ordinateur fait sur
l'appareil même (aucun texte n'est envoyé) : un clic télécharge la langue la
première fois (bouton « Télécharger la langue »). Les autres navigateurs et
les téléphones ne l'ont pas encore : le panneau y montre le dictionnaire de
la langue, puis l'anglais. L'arabe s'affiche de droite à gauche. Les nombres
et les dates suivent la langue, en chiffres latins.

## Rachelle

- Elle reçoit la langue et le pays de l'appareil. En français, elle répond
  comme avant ; dans toute autre langue, ses réponses écrites sur ce PC sont
  en anglais (une trentaine de sujets, dont les paiements), traduites par
  l'appareil s'il sait le faire. Un sujet sans texte anglais répond en
  français en le disant.
- Une question écrite dans une autre langue est reconnue par ses mots-clés
  (espagnol, portugais, allemand, italien, russe, chinois, arabe, japonais,
  coréen, hindi, turc), ou par sa traduction en anglais faite sur l'appareil.
- Si une IA est configurée, elle répond directement dans la langue de
  l'appareil, quelle qu'elle soit.
- Le garde-fou de sécurité vaut dans toutes les langues : la question
  traduite passe aussi par lui.
- La voix lit la réponse dans la langue du panneau, avec une voix de
  l'appareil quand il en a une.

## Limites connues

Tous les textes du panneau lui-même sont traduits. Restent en français, sauf
traduction de l'appareil : les textes que le bot calcule (raisonnement du
jour, notes sur les pays, les bourses et les courtiers, rapport de sécurité,
diagnostics), le rapport quotidien et les alertes par e-mail et WhatsApp. Les
actualités gardent la langue de leur source.

Ajouter une langue écrite : un fichier `i18n/<code>.js`, de même forme que
les autres, et son code dans `WRITTEN` (`i18n.js`). Les tests
(`tests/test_dictionnaires.py`) vérifient que chaque texte de la page a son
anglais et que les parties variables sont gardées.
