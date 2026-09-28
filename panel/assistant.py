"""Assistant du panneau : fenêtre de dialogue sur les objectifs du bot, les
marchés crypto et financiers, le trading, la connexion et la configuration
d'un téléphone, l'utilisation de l'interface et l'accès au panneau.

Sécurité d'abord (garde-fou appliqué AVANT toute réponse) :
- une information secrète collée (clé API, secret, mot de passe, code, phrase
  de récupération) est masquée : jamais envoyée à une IA, jamais enregistrée,
  et l'utilisateur est invité à la révoquer ;
- toute demande qui mettrait sa sécurité en jeu (révéler une clé ou le
  fichier .env, contourner une protection, exposer le panneau sur Internet,
  activer les retraits, déplacer des fonds) est refusée en une phrase, sans
  autre détail ;
- l'assistant ne voit que des données publiques (état du bot, marchés) et
  ne peut rien modifier : il n'a aucun accès aux clés ni aux ordres.

Réponses : base de connaissances intégrée (hors ligne, sans clé), avec les
chiffres du moment. Si une IA est configurée pour la veille (Claude de
préférence), elle rédige des réponses plus libres, dans le même périmètre ;
PANEL_ASSISTANT_IA=false pour s'en passer.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

import market_watch as mw

REFUSED = "🔒 Demande refusée pour votre sécurité."
MASKED = ("🔒 Message masqué : il semblait contenir une information secrète (clé, mot de "
          "passe ou code). Il n'a été ni envoyé à une IA, ni enregistré. Si c'était une vraie "
          "clé ou un vrai mot de passe, révoquez-le : sur Binance, « Gestion des API » → "
          "supprimer la clé, puis créez-en une nouvelle avec la saisie masquée "
          "(python trendguard_bot.py set-keys).")
SCOPE = ("Je réponds uniquement sur : les objectifs du bot, les marchés crypto et financiers, "
         "le trading, la connexion et la configuration de votre téléphone, l'utilisation de "
         "l'interface et votre accès au panneau.")
SUGGESTIONS = ["Quel est l'objectif du bot ?", "Comment va le marché crypto ?",
               "Et la bourse ?", "Connecter mon téléphone", "Configurer mon téléphone",
               "Comment utiliser le panneau ?", "Créer mon accès (mot de passe)",
               "Qu'est-ce qu'un stop ?"]
MAX_MESSAGE = 1200


def norm(text: str) -> str:
    """Minuscules, sans accents, ponctuation → espaces."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9.$@]+", " ", t).strip()


# ══════════════════════════════════════════════════════════════════════
# GARDE-FOU
# ══════════════════════════════════════════════════════════════════════

SECRET = (r"(cles?|keys?|secrets?|mots? de passe|password|passwd|mdp|tokens?|jetons?|seed|"
          r"phrase de recuperation|phrase secrete|mnemonique|cle privee|private key|cookies?|"
          r"identifiants?|credentials?|code 2fa|code a2f|otp)")
STOPWORDS = {"le", "la", "les", "de", "du", "des", "un", "une", "et", "pour", "mon", "ma", "mes",
             "je", "tu", "il", "que", "qui", "comment", "est", "sur", "dans", "au", "avec", "pas",
             "ne", "ce", "on", "en", "the", "to", "my", "how", "is", "and", "of", "for", "what",
             "you", "it", "in", "on", "with", "do", "can", "a", "i", "bot", "panneau"}
REQUEST_RULES = (
    # Révéler une donnée d'accès.
    re.compile(r"\b(donne|montre|affiche|revele|lis|dis|envoie|copie|recupere|trouve|devine|"
               r"print|show|give|reveal|tell|read|display|send|leak|dump)\w*\b(\s+\S+){0,3}?\s+"
               + SECRET + r"\b"),
    re.compile(r"\b(quel|quelle|quels|quelles|c est quoi|what is|what s)\b(\s+(est|sont|is|are))?"
               r"(\s+(le|la|les|l|mon|ma|mes|ton|ta|tes|son|sa|ses|votre|vos|the|my|your))?\s+"
               + SECRET + r"\b(?!\s+(choisir|utiliser|creer|mettre|faut|conseill|solide|fort))"),
    re.compile(r"(\.env\b|fichier env|variables? d environnement).*\b(contenu|contient|affiche|"
               r"lis|lire|montre|donne|copie|envoie|voir|ouvre)|\b(contenu|contient|affiche|lis|"
               r"lire|montre|donne|copie|envoie|voir|ouvre)\w*\b.*(\.env\b|fichier env)"),
    # Contourner une protection.
    re.compile(r"\b(contourn|desactiv|supprim|enlev|retir|pirat|hack|crack|bypass|forc|brute)\w*"
               r"\b(\s+\S+){0,4}?\s+(le |la |les |l )?(mot de passe|password|mdp|securite|"
               r"protection|verrou|authentification|pare feu|firewall|csrf|garde fou|confirmation)"),
    # Exposer le panneau sur Internet.
    re.compile(r"\b(ouvr|redirig|transfer|expos|publi|mett)\w*\b(\s+\S+){0,4}?\s+(les |le |un )?"
               r"(ports?\b|panneau sur internet|sur internet)|port forwarding|"
               r"redirection de ports?|\bngrok\b|\bdmz\b"),
    # Déplacer des fonds, activer les retraits.
    re.compile(r"\b(retir|withdraw|transfer|vir|deplac|envo|sort|vid)\w*\s+(tous |toutes )?"
               r"(les |mes |mon |ma |the |my |l )(fonds|argent|capital|economies|usdt|cryptos?|"
               r"bitcoins?|btc|solde)\b"),
    re.compile(r"\bretraits?\s+(de\s+)?(mes|mon|des|du)\s+(fonds|argent|capital|usdt|cryptos?)\b"),
    re.compile(r"\b(activ|autoris|enable|allow)\w*\s+(les |le |the )?(retraits?|withdrawals?)\b"),
    re.compile(r"\b(vers|to)\s+(mon|ma|une|un|my|a)\s+(wallet|portefeuille externe|"
               r"compte bancaire|metamask|ledger|iban)\b"),
)
PASTED = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),                               # clés d'IA
    re.compile(r"(?i)\b(mot de passe|password|passwd|mdp|pin)\s*(est|=|:|is|c'est|c’est)\s*"
               r"(?=\S*[\d!@#$%^&*_+=?])\S{4,}"),
    re.compile(r"(?i)\b(code|2fa|a2f|otp)\b\D{0,20}\b\d{6}\b"),
)


def _long_token(raw: str) -> bool:
    """Clé API, secret ou clé privée collés : longue suite de lettres ET de
    chiffres (les adresses web sont ignorées)."""
    for tok in re.split(r"\s+", raw):
        tok = tok.strip("\"'`.,;:()[]{}<>")
        if len(tok) < 32 or "://" in tok or "www." in tok or "/" in tok:
            continue
        if re.fullmatch(r"[A-Za-z0-9_\-+=]{32,}", tok) and re.search(r"\d", tok) \
                and re.search(r"[A-Za-z]", tok):
            return True
    return False


def _seed_phrase(raw: str) -> bool:
    words = raw.strip().split()
    if len(words) not in (12, 15, 18, 21, 24):
        return False
    return (all(re.fullmatch(r"[a-z]{3,8}", w) for w in words)
            and not STOPWORDS & set(words))


def guard(message: str) -> Optional[Tuple[str, str]]:
    """(genre, réponse) si le message doit être masqué ou refusé, sinon None."""
    if _long_token(message) or _seed_phrase(message) or any(p.search(message) for p in PASTED):
        return "masked", MASKED
    text = norm(message)
    if any(r.search(text) for r in REQUEST_RULES):
        return "refused", REFUSED
    return None


def redact(text: str) -> str:
    """Filet de sécurité sur les réponses d'une IA."""
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}", "•••", text)
    return re.sub(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b",
                  "•••", text)


# ══════════════════════════════════════════════════════════════════════
# BASE DE CONNAISSANCES (réponses hors ligne, avec les chiffres du moment)
# ══════════════════════════════════════════════════════════════════════

def _pc(v: Any, d: int = 1) -> str:
    try:
        return f"{float(v):+.{d}f} %".replace(".", ",")
    except (TypeError, ValueError):
        return "–"


def _num(v: Any, d: int = 2) -> str:
    try:
        return f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return "–"


def _headlines(news: Dict[str, Any], category: Optional[str], n: int) -> List[str]:
    items = [i for i in news.get("items") or [] if category is None or i.get("category") == category]
    return [f"- {i['title']} ({i['source']})" for i in items[:n]]


def a_objectives(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    mode = "réel" if st.get("mode") == "live" else "paper (argent fictif, prix réels)"
    return (
        "**Objectif** : capter les grandes tendances des cryptos tout en limitant chaque perte.\n"
        f"- 21 cryptos cotées en USDT sur Binance Spot, mode actuel : {mode}.\n"
        "- Achat quand une crypto casse son plus haut des 30 derniers jours et que sa "
        "tendance de fond (90 jours) est positive, seulement si le bitcoin est au-dessus "
        "de sa moyenne 150 jours (marché haussier).\n"
        "- Chaque achat risque 1 % du capital au plus ; 8 positions maximum, 6 % de risque "
        "cumulé au plus.\n"
        "- Vente quand le cours clôture sous un stop qui monte avec le prix ; stop "
        "catastrophe posé chez Binance contre les krachs ; arrêt d'urgence à −40 %.\n"
        "- Historique réel (frais compris) : environ +37 % par an de 2023 à 2026, avec une "
        "pire baisse de −34 %. 35 à 50 % de trades gagnants : les gains, plus grands que "
        "les pertes, font le résultat. Aucune garantie pour l'avenir.")


def a_status(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    state = {"running": "en marche", "stopped": "arrêté", "starting": "en démarrage",
             "stopping": "en cours d'arrêt", "restarting": "en relance automatique"}.get(
        st.get("state"), st.get("state") or "inconnu")
    lines = [f"Le bot est **{state}** (mode {'réel' if st.get('mode') == 'live' else 'paper'})."]
    if st.get("equity") is not None:
        ch = ""
        if st.get("start_equity"):
            ch = f", {_pc((st['equity'] / st['start_equity'] - 1) * 100)} depuis le départ"
        lines.append(f"- Capital : {_num(st['equity'])} USDT{ch}.")
    lines.append(f"- Positions : {st.get('positions', 0)} / {st.get('max_positions', 8)}.")
    if st.get("drawdown_pct") is not None:
        lines.append(f"- Baisse depuis le plus haut : {_pc(st['drawdown_pct'])}.")
    if st.get("regime_bull") is not None:
        lines.append("- Marché : " + ("haussier, achats autorisés." if st["regime_bull"]
                                      else "baissier, aucun achat."))
    why = ((ctx.get("reasoning") or {}).get("current") or {}).get("lines") or []
    if why:
        lines.append("Son dernier raisonnement : " + " ".join(why[:2]))
    return "\n".join(lines)


def a_crypto_market(ctx: Dict[str, Any]) -> str:
    news = ctx.get("news") or {}
    m = news.get("markets") or {}
    c, f = m.get("crypto"), m.get("fear_greed")
    lines = ["**Marché crypto en ce moment** :"]
    btc = (news.get("crypto_prices") or {}).get("btc") or {}
    if btc.get("price"):
        lines.append(f"- Bitcoin : {_num(btc['price'], 0)} $ ({_pc(btc.get('change_pct'))} sur 24 h).")
    if c:
        lines.append(f"- Capitalisation totale : {_num(c['market_cap_usd'] / 1e9, 0)} milliards $ "
                     f"({_pc(c.get('market_cap_change_24h_pct'))} sur 24 h) ; dominance du "
                     f"bitcoin {_num(c.get('btc_dominance_pct'), 1)} %.")
    if f:
        lines.append(f"- Peur & Avidité : {f['value']}/100, « {f['label']} » (0 = peur extrême, "
                     f"100 = euphorie).")
    st = ctx.get("status") or {}
    if st.get("regime_bull") is not None:
        lines.append("- Pour le bot : " + ("BTC au-dessus de sa moyenne 150 jours, achats "
                                           "autorisés." if st["regime_bull"] else
                                           "BTC sous sa moyenne 150 jours, aucun achat."))
    heads = _headlines(news, "crypto", 3)
    if heads:
        lines += ["À la une :"] + heads
    if len(lines) == 1:
        lines.append("- Données en cours de chargement : réessayez dans une minute.")
    return "\n".join(lines)


def a_finance(ctx: Dict[str, Any]) -> str:
    news = ctx.get("news") or {}
    quotes = {q["id"]: q for q in (news.get("markets") or {}).get("quotes") or []}
    lines = ["**Marchés financiers (variation du jour)** :"]
    for qid in ("sp500", "nasdaq", "cac40", "gold", "oil", "eurusd", "us10y"):
        q = quotes.get(qid)
        if q:
            unit = " %" if q.get("unit") == "%" else ""
            lines.append(f"- {q['name']} : {_num(q['price'], 2 if q['price'] < 1000 else 0)}{unit} "
                         f"({_pc(q.get('change_pct'), 2)}).")
    heads = _headlines(news, "finance", 3)
    if heads:
        lines += ["À la une :"] + heads
    if len(lines) == 1:
        lines.append("- Cours en cours de chargement : réessayez dans une minute.")
    lines.append("Le bot ne trade que des cryptos, mais la bourse, le dollar et les taux "
                 "influencent souvent leur tendance.")
    return "\n".join(lines)


def a_news(ctx: Dict[str, Any]) -> str:
    heads = _headlines(ctx.get("news") or {}, None, 5)
    if not heads:
        return "Les actualités se chargent : réessayez dans une minute."
    return "**Dernières actualités** :\n" + "\n".join(heads)


def a_phone(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    urls = st.get("lan_urls") or []
    now = (f"Accès Wi-Fi déjà actif : ouvrez {urls[0]} sur le téléphone." if urls else
           "L'accès Wi-Fi n'est pas encore actif sur ce panneau.")
    return (
        "**Se connecter depuis un téléphone** (même Wi-Fi que le PC) :\n"
        "- 1. Sur le PC, créez votre accès : python trendguard_bot.py set-panel-password "
        "(saisie masquée) et répondez « o » à « accès depuis un téléphone ».\n"
        "- 2. Le panneau s'ouvre au Wi-Fi au prochain démarrage de l'ordinateur ; tout de "
        "suite : tâche VS Code « Panneau — accès téléphone (port 8766) ».\n"
        "- 3. Windows demande d'autoriser Python : cochez « Réseaux privés » seulement.\n"
        "- 4. Sur le téléphone, ouvrez l'adresse affichée dans Réglages ▸ Sur votre "
        "téléphone (http://adresse-du-PC:port) et entrez le mot de passe.\n"
        f"{now}\n"
        "Hors de chez vous (4G) : n'ouvrez jamais de port sur votre box ; installez un VPN "
        "gratuit comme Tailscale sur le PC et le téléphone. Évitez les Wi-Fi publics.")


def a_phone_setup(ctx: Dict[str, Any]) -> str:
    return (
        "**Configurer le téléphone** (le panneau devient une application) :\n"
        "- Android (Chrome) : ouvrez le panneau, menu ⋮ puis « Ajouter à l'écran d'accueil » "
        "ou « Installer l'application ».\n"
        "- iPhone (Safari) : bouton Partager puis « Sur l'écran d'accueil ».\n"
        "- L'icône TrendGuard s'ouvre alors en plein écran, avec la barre d'onglets en bas.\n"
        "- Alertes sur le téléphone : WhatsApp ou e-mail avec python alerts.py configurer "
        "(sur le PC), puis python alerts.py tester.\n"
        "Il n'y a pas de fichier APK : il faudrait publier le panneau sur Internet, ce qui "
        "mettrait le bot en danger.")


def a_interface(ctx: Dict[str, Any]) -> str:
    return (
        "**Utiliser le panneau** :\n"
        "- AUTO (en haut à droite) démarre l'automatisation ; ARRÊTER l'arrête proprement, "
        "sans relance. En mode réel, une confirmation est demandée.\n"
        "- Tableau de bord : capital, bandeau d'actualités (il s'arrête sous la souris, un "
        "clic ouvre la page), « Ce que pense le bot », positions et alertes.\n"
        "- Actualités : marchés crypto et financiers, articles filtrables.\n"
        "- Graphiques : un clic ouvre le détail (bougies, achats, ventes, stops, zoom).\n"
        "- Cryptos : les 21 cryptos, la raison du choix du bot, filtre « Surveillées ».\n"
        "- Positions, Veille, Journal ; Réglages : démarrage avec l'ordinateur, alertes, "
        "thème, téléphone.\n"
        "- Ce bouton Assistant répond à vos questions ; il ne peut rien modifier.")


def a_account(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    active = ("Un mot de passe est déjà défini pour ce panneau." if st.get("password") else
              "Aucun mot de passe n'est défini : le panneau n'est ouvert que sur ce PC.")
    return (
        "**Votre accès au panneau** : il n'y a pas d'inscription publique, car le panneau "
        "commande un bot qui gère de l'argent. L'accès est réservé au propriétaire :\n"
        "- Sur le PC lui-même, pas de mot de passe : seul ce PC y accède.\n"
        "- Pour un téléphone, créez ou changez le mot de passe sur le PC : python "
        "trendguard_bot.py set-panel-password (10 caractères minimum, saisie masquée ; "
        "tâche VS Code « Panneau — définir le mot de passe »).\n"
        "- Chaque appareil reste connecté 30 jours ; « Se déconnecter » dans Réglages.\n"
        "- Pour déconnecter tous les appareils : changez le mot de passe puis redémarrez "
        "l'ordinateur.\n"
        f"{active}\n"
        "Ne tapez jamais ce mot de passe dans cette fenêtre.")


def a_keys(ctx: Dict[str, Any]) -> str:
    return (
        "**Clés Binance, en sécurité** :\n"
        "- Créez la clé sur Binance avec la lecture et le trading Spot, JAMAIS le droit de "
        "retrait, et limitez-la à l'adresse IP de votre connexion si possible.\n"
        "- Enregistrez-la sur le PC avec python trendguard_bot.py set-keys : saisie masquée, "
        "vérifiée auprès de Binance, stockée dans le fichier .env privé.\n"
        "- Ne la collez jamais dans une conversation, un e-mail ou ce panneau. Si c'est "
        "arrivé : supprimez-la sur Binance et créez-en une nouvelle.\n"
        "- Activez la double authentification (2FA) sur votre compte Binance.")


GLOSSARY = {
    "stop": ("**Stop** : niveau de prix où le bot vend pour limiter la perte. Le stop de "
             "clôture monte avec le prix (5 × la volatilité sous le plus haut ; 2 × en marché "
             "baissier) et n'est vérifié qu'à la clôture quotidienne, pour ignorer les mèches. "
             "Le stop catastrophe, posé chez Binance un peu plus bas, protège d'un krach entre "
             "deux clôtures."),
    "breakout": ("**Cassure** : la clôture dépasse le plus haut des 30 derniers jours. C'est le "
                 "signal d'entrée du bot, à condition que la tendance sur 90 jours soit positive "
                 "et le marché haussier."),
    "regime": ("**Régime du marché** : le bot compare le bitcoin à sa moyenne des 150 derniers "
               "jours. Au-dessus, marché haussier : achats autorisés. En dessous : aucun achat "
               "et stops resserrés pour protéger les gains."),
    "risk": ("**Risque de 1 %** : la taille de chaque achat est calculée pour qu'en touchant son "
             "stop, la perte soit d'environ 1 % du capital. Une crypto agitée reçoit donc une "
             "position plus petite qu'une crypto calme."),
    "r": ("**R** : multiple du risque pris. +3 R = un gain égal à trois fois la perte prévue au "
          "départ ; −1 R = la perte prévue."),
    "drawdown": ("**Baisse depuis le plus haut (drawdown)** : écart entre le capital actuel et "
                 "son record. Au-delà de −40 %, l'arrêt d'urgence bloque les achats."),
    "paper": ("**Mode paper** : le bot suit les vrais prix de Binance mais avec de l'argent "
              "fictif. Le mode réel exige des clés, une double confirmation dans le .env, et "
              "une confirmation dans le panneau."),
    "spread": ("**Écart achat/vente et carnet d'ordres** : si l'écart dépasse 0,5 % ou si trop "
               "peu d'offres existent près du prix, le bot diffère l'achat et réessaie toutes "
               "les 5 minutes pendant 6 heures, pour ne pas acheter à un mauvais prix."),
    "fees": ("**Frais** : 0,1 % par achat et par vente sur Binance Spot (moins avec BNB). Les "
             "résultats historiques affichés les incluent, avec 0,1 % de glissement de prix."),
    "trend": ("**Suivi de tendance** : acheter ce qui monte déjà et le garder tant que la hausse "
              "dure. Beaucoup de petites pertes, quelques grands gains qui font le résultat."),
    "fng": ("**Indice Peur & Avidité** : baromètre de l'humeur du marché crypto, de 0 (peur "
            "extrême) à 100 (euphorie). Le bot ne s'en sert pas pour décider ; il est affiché "
            "à titre d'information."),
    "dominance": ("**Dominance du bitcoin** : part du bitcoin dans la valeur totale des "
                  "cryptos. Quand elle monte, les autres cryptos font souvent moins bien."),
}


def _gloss(key: str) -> Callable[[Dict[str, Any]], str]:
    return lambda ctx: GLOSSARY[key]


def a_advice(ctx: Dict[str, Any]) -> str:
    return ("Je ne donne ni conseil d'investissement personnalisé ni prévision de prix : "
            "personne ne connaît le prix de demain. Le bot applique des règles fixes, testées "
            "sur 8 ans, et risque 1 % par achat. Sa décision du jour et les cryptos qu'il "
            "surveille sont dans « Ce que pense le bot ». N'investissez que ce que vous pouvez "
            "perdre.")


def a_alerts(ctx: Dict[str, Any]) -> str:
    return ("**Alertes** : Telegram, e-mail et WhatsApp. Sur le PC : python alerts.py "
            "configurer (saisie masquée), puis python alerts.py tester, ou le bouton « Tester » "
            "dans Réglages. Par défaut, seules les alertes critiques partent par e-mail et "
            "WhatsApp (ALERT_LEVEL=all pour tout recevoir).")


def a_autonomy(ctx: Dict[str, Any]) -> str:
    au = (ctx.get("status") or {}).get("autonomy") or {}
    auto = "activé" if au.get("autostart") else "désactivé"
    return ("**Autonomie** : AUTO lance un superviseur qui relance le bot s'il plante ou se "
            "bloque. Le démarrage avec l'ordinateur est " + auto + " (Réglages ▸ Autonomie). "
            "Le PC ne se met pas en veille tout seul pendant que le bot tourne. ARRÊTER est "
            "respecté : aucune relance, même au redémarrage, jusqu'au prochain AUTO.")


def a_watch(ctx: Dict[str, Any]) -> str:
    return ("**Veille** : chaque jour, le bot lit les annonces officielles de Binance (une "
            "crypto que Binance retire n'est plus achetée) et, si des IA sont configurées, "
            "leur avis sur l'actualité, à titre de conseil seulement. Le détail est dans "
            "l'onglet Veille.")


def a_hello(ctx: Dict[str, Any]) -> str:
    return "Bonjour ! " + SCOPE + " Choisissez une question ci-dessous ou écrivez la vôtre."


# (identifiant, mots-clés, réponse, actions)
Action = Dict[str, str]
TOPICS: Tuple[Tuple[str, Tuple[str, ...], Callable[[Dict[str, Any]], str], List[Action]], ...] = (
    ("hello", ("bonjour", "salut", "hello", "coucou", "bonsoir", "aide", "help",
               "que peux tu", "qui es tu"), a_hello, []),
    ("objectives", ("objectif", "objectifs", "but", "strategie", "que fait le bot",
                    "a quoi sert", "fonctionne le bot", "fonctionnement du bot", "comment marche",
                    "rendement", "performance attendue", "gagner", "principe"), a_objectives,
     [{"label": "Ce que pense le bot", "href": "#dash"}]),
    ("status", ("etat", "capital", "positions", "combien", "gagne", "perdu", "resultat",
                "en marche", "tourne", "solde", "portefeuille du bot"), a_status,
     [{"label": "Tableau de bord", "href": "#dash"}, {"label": "Positions", "href": "#positions"}]),
    ("crypto_market", ("marche crypto", "~crypto", "~cryptos", "~bitcoin", "~btc", "~ethereum",
                       "comment va le marche", "~marche", "tendance du marche", "altcoin"),
     a_crypto_market, [{"label": "Marchés et actualités", "href": "#news"}]),
    ("finance", ("bourse", "action", "actions", "s p 500", "sp500", "cac", "nasdaq", "dow",
                 "l or", "gold", "petrole", "~dollar", "~euro", "taux", "fed", "bce", "inflation",
                 "finance", "wall street"), a_finance, [{"label": "Marchés financiers", "href": "#news"}]),
    ("news", ("actualite", "actualites", "news", "nouvelles", "infos", "information du jour"),
     a_news, [{"label": "Toutes les actualités", "href": "#news"}]),
    ("phone", ("telephone", "smartphone", "mobile", "portable", "a distance", "distance", "wifi",
               "wi fi", "connecter", "connexion", "4g", "exterieur", "vpn", "android", "iphone"),
     a_phone, [{"label": "Réglages ▸ téléphone", "href": "#settings"}]),
    ("phone_setup", ("configurer le telephone", "configurer mon telephone", "configurer",
                     "installer", "ecran d accueil", "application", "apk", "appli", "pwa"),
     a_phone_setup, [{"label": "Réglages", "href": "#settings"}]),
    ("interface", ("interface", "~panneau", "utiliser", "bouton", "onglet", "menu", "~auto",
                   "arreter", "graphique", "tableau de bord", "comment ca marche", "mode d emploi"),
     a_interface, [{"label": "Graphiques", "href": "#charts"}, {"label": "Cryptos", "href": "#assets"}]),
    ("account", ("compte", "inscription", "inscrire", "enregistrer", "enregistrement",
                 "utilisateur", "mot de passe", "identifiant", "login", "se connecter",
                 "deconnecter", "acces", "creer mon acces"), a_account,
     [{"label": "Réglages", "href": "#settings"}]),
    ("keys", ("cle api", "cles api", "api", "binance", "2fa", "double authentification",
              "droit de retrait", "securite", "securiser"),
     a_keys, []),
    ("alerts", ("alerte", "alertes", "whatsapp", "mail", "e mail", "email", "notification",
                "telegram", "sms"), a_alerts, [{"label": "Réglages ▸ alertes", "href": "#settings"}]),
    ("autonomy", ("autonome", "autonomie", "redemarrage", "redemarrer", "demarrer avec",
                  "ordinateur", "veille du pc", "plantage", "superviseur", "eteint"), a_autonomy,
     [{"label": "Réglages ▸ autonomie", "href": "#settings"}]),
    ("watch", ("veille", "ia", "intelligence", "claude", "gpt", "annonce", "annonces",
               "retrait de la cote", "delisting", "bloquee", "bloquees"), a_watch,
     [{"label": "Veille", "href": "#watch"}]),
    ("advice", ("dois je", "acheter", "vendre", "investir", "conseil", "prediction", "prevision",
                "va monter", "va baisser", "meilleure crypto", "quoi acheter"), a_advice, []),
    ("stop", ("stop", "stop loss", "stop suiveur", "stop catastrophe", "trailing"), _gloss("stop"), []),
    ("breakout", ("cassure", "breakout", "plus haut"), _gloss("breakout"), []),
    ("regime", ("regime", "moyenne 150", "moyenne mobile", "haussier", "baissier"),
     _gloss("regime"), []),
    ("risk", ("risque", "1 pour cent", "taille de position", "combien il mise"),
     _gloss("risk"), []),
    ("r", ("en r", "multiple", "r multiple", "que veut dire r"), _gloss("r"), []),
    ("drawdown", ("drawdown", "baisse depuis", "arret d urgence", "kill"), _gloss("drawdown"), []),
    ("paper", ("paper", "argent fictif", "reel", "live", "mode reel", "testnet"),
     _gloss("paper"), []),
    ("spread", ("ecart", "spread", "carnet", "differe", "ruse"), _gloss("spread"), []),
    ("fees", ("frais", "commission", "fees"), _gloss("fees"), []),
    ("trend", ("suivi de tendance", "trend following", "tendance"), _gloss("trend"), []),
    ("fng", ("peur", "avidite", "fear", "greed"), _gloss("fng"), []),
    ("dominance", ("dominance",), _gloss("dominance"), []),
)
TOPIC_BY_ID = {t[0]: t for t in TOPICS}


def match(message: str) -> List[Tuple[int, str]]:
    """Sujets reconnus, du plus probable au moins probable."""
    text = f" {norm(message)} "
    scores = []
    for order, (tid, keys, _fn, _acts) in enumerate(TOPICS):
        score = 0
        for k in keys:
            weak = k.startswith("~")                  # mot trop général : poids faible
            k = k.lstrip("~")
            if f" {k} " in text:
                score += 1 if weak else 2 + k.count(" ") * 2   # une expression pèse plus
        if score:
            scores.append((score, -order, tid))
    scores.sort(reverse=True)
    return [(s, tid) for s, _o, tid in scores]


def local_answer(message: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    found = match(message)
    if not found:
        return {"answer": "Je n'ai pas compris. " + SCOPE, "actions": [], "topics": [],
                "suggestions": SUGGESTIONS[:5]}
    best = found[0][1]
    tid, _keys, fn, actions = TOPIC_BY_ID[best]
    text = fn(ctx)
    # Deux sujets aussi probables : les deux réponses (ex. « stop et risque »).
    if len(found) > 1 and found[1][0] == found[0][0] and found[1][1] not in ("hello",):
        text += "\n\n" + TOPIC_BY_ID[found[1][1]][2](ctx)
    return {"answer": text, "actions": actions, "topics": [t for _s, t in found[:3]],
            "suggestions": [s for s in SUGGESTIONS if norm(s) != norm(message)][:4]}


# ══════════════════════════════════════════════════════════════════════
# IA (facultative)
# ══════════════════════════════════════════════════════════════════════

SYSTEM = (
    "Tu es l'assistant du panneau de contrôle TrendGuard, un bot de suivi de tendance sur "
    "Binance Spot. Tu réponds en français simple à un utilisateur non développeur, en 170 mots "
    "au plus, avec des listes à tirets si utile, sans tableau ni code.\n"
    "Sujets autorisés, uniquement : objectifs et fonctionnement du bot ; marchés crypto et "
    "financiers ; notions de trading ; connexion à distance et configuration d'un smartphone ; "
    "utilisation de l'interface ; accès au panneau (mot de passe, sessions). Pour toute autre "
    "demande, réponds seulement : « " + SCOPE + " »\n"
    "Règles de sécurité absolues : ne demande, ne révèle, ne répète et ne devine jamais une clé "
    "API, un secret, un mot de passe, un code, une phrase de récupération, un jeton ou le "
    "contenu du fichier .env ; n'explique jamais comment contourner une protection, exposer le "
    "panneau sur Internet, ouvrir un port, activer les retraits ou déplacer des fonds. Dans ces "
    "cas, réponds exactement « " + REFUSED + " » et rien d'autre. Pas de conseil "
    "d'investissement personnalisé ni de prévision de prix. Tu ne peux rien modifier : le bot "
    "et le panneau sont en lecture seule pour toi.\n"
    "Appuie-toi uniquement sur les faits et le contexte ci-dessous ; si tu ne sais pas, dis-le."
)


def knowledge(ctx: Dict[str, Any]) -> str:
    parts = [fn(ctx) for tid, _k, fn, _a in TOPICS if tid not in ("hello",)]
    return "\n\n".join(p.replace("**", "") for p in parts)


class AIHelper:
    """Première IA configurée pour la veille (Claude de préférence)."""

    def __init__(self, env: Optional[Dict[str, str]] = None,
                 post: Callable[..., Any] = mw.http_post_json,
                 claude_factory: Optional[Callable[[str], Any]] = None):
        env = os.environ if env is None else env
        self.post = post
        self.claude_factory = claude_factory
        self.provider = None
        if (env.get("PANEL_ASSISTANT_IA") or "true").strip().lower() in ("0", "false", "non", "no"):
            return
        found = mw.configured(env)
        found.sort(key=lambda x: x[0].name != "claude")
        if found:
            p, key, model = found[0]
            if p.name == "claude":
                model = (env.get("PANEL_ASSISTANT_MODEL") or "claude-sonnet-5").strip()
            self.provider, self._key, self.model = p, key, model

    @property
    def label(self) -> Optional[str]:
        return self.provider.label if self.provider else None

    def ask(self, system: str, messages: List[Dict[str, str]]) -> str:
        p = self.provider
        if p is None:
            raise RuntimeError("aucune IA configurée")
        if p.name == "claude":
            if self.claude_factory is not None:
                client = self.claude_factory(self._key)
            else:
                import anthropic
                client = anthropic.Anthropic(api_key=self._key, timeout=60.0, max_retries=1)
            r = client.messages.create(model=self.model, max_tokens=1024, system=system,
                                       messages=messages)
            if r.stop_reason == "refusal":
                return REFUSED
            return next((b.text for b in r.content if b.type == "text"), "")
        resp = self.post(p.url, {"model": self.model,
                                 "messages": [{"role": "system", "content": system}] + messages},
                         {"Authorization": f"Bearer {self._key}"}, timeout=60.0)
        return resp["choices"][0]["message"]["content"] or ""


class Assistant:
    def __init__(self, ai: Optional[AIHelper] = None):
        self.ai = ai

    def info(self) -> Dict[str, Any]:
        label = self.ai.label if self.ai else None
        return {"ai": label, "suggestions": SUGGESTIONS, "welcome": (
            "Bonjour ! Je suis l'assistant du panneau. " + SCOPE
            + " 🔒 Je ne traite aucune information secrète : ne tapez jamais de clé, de mot "
              "de passe ni de code ici.")}

    def reply(self, message: Any, history: Any, ctx_fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        message = str(message or "").strip()[:MAX_MESSAGE]
        if not message:
            return {"answer": SCOPE, "actions": [], "suggestions": SUGGESTIONS[:4],
                    "source": "local"}
        g = guard(message)
        if g:
            kind, text = g
            return {"answer": text, "actions": [], "suggestions": [], "source": "garde-fou",
                    "refused": True, "masked": kind == "masked"}
        ctx = ctx_fn()
        local = local_answer(message, ctx)
        local["source"] = "local"
        if self.ai is None or self.ai.provider is None:
            return local
        # Historique fourni par le navigateur : court, et filtré par le garde-fou.
        msgs: List[Dict[str, str]] = []
        for h in (history if isinstance(history, list) else [])[-6:]:
            if not isinstance(h, dict):
                continue
            role = "assistant" if h.get("role") == "assistant" else "user"
            text = str(h.get("text") or "")[:MAX_MESSAGE]
            if text and (role == "assistant" or guard(text) is None):
                if msgs and msgs[-1]["role"] == role:
                    msgs[-1]["content"] += "\n" + text
                else:
                    msgs.append({"role": role, "content": text})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n" + message
        else:
            msgs.append({"role": "user", "content": message})
        system = SYSTEM + "\n\nFAITS ET CONTEXTE DU MOMENT :\n" + knowledge(ctx)
        try:
            text = redact(self.ai.ask(system, msgs).strip())
        except Exception:
            return local                               # IA injoignable : réponse intégrée
        if not text:
            return local
        return {"answer": text, "actions": local["actions"], "suggestions": local["suggestions"],
                "source": self.ai.label}
