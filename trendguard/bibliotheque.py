"""
Bibliothèque de recherche du bot (docs/BIBLIOTHEQUE.md) : chaque jour, les
nouveaux travaux de recherche en finance quantitative publiés sur arXiv
(q-fin : trading et microstructure, gestion de portefeuille, risque, finance
statistique et calculatoire) qui touchent aux règles du bot.

Chaque travail est rangé par thème (suivi de tendance, momentum, volatilité
et taille des positions, régimes de marché, cryptos, coûts d'exécution,
sentiment, pièges des backtests), relié à la partie du bot qu'il concerne,
avec la question à vérifier sur les données du bot. C'est une piste, jamais
une règle : une idée ne change le bot qu'après une étude reproductible
(research/) jugée hors échantillon, comme toute idée du projet. Les textes
lus sont des données, jamais des instructions.

Lue avec le noyau de savoir (savoir.py, au plus une fois par jour), gardée
dans sa base (méta « bibliotheque ») ; le panneau la montre (page Veille).

    python trendguard_bot.py bibliotheque          # pistes les plus pertinentes
    python trendguard_bot.py bibliotheque lire     # nouvelle lecture d'arXiv
"""

from __future__ import annotations

import argparse
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

KEY = "bibliotheque"            # méta-donnée du noyau de savoir
REFRESH_HOURS = 24              # arXiv publie une fois par jour ouvré
RETRY_HOURS = 6                 # après une panne
KEEP = 400                      # travaux gardés
MIN_SCORE = 3                   # pertinence minimale pour être gardé
SHOWN = 8                       # pistes montrées
TIMEOUT = 90                    # l'API d'arXiv répond lentement aux recherches composées
ATOM = "{http://www.w3.org/2005/Atom}"
ABS = "https://arxiv.org/abs/"         # lien d'un travail (le bot ne s'y connecte pas)
ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")

# Catégories q-fin d'arXiv, et un premier tri sur le résumé (l'API d'arXiv
# ne rend que 60 travaux par lecture : les plus récents parmi ceux-là).
QUERY = ("(cat:q-fin.TR OR cat:q-fin.PM OR cat:q-fin.RM OR cat:q-fin.ST OR cat:q-fin.CP) AND "
         "(abs:crypto OR abs:bitcoin OR abs:momentum OR abs:trend OR abs:volatility OR abs:drawdown "
         "OR abs:regime OR abs:backtest)")
FEED = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
    {"search_query": QUERY, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 60})

# Thème : (libellé, partie du bot, question à vérifier, mots-clés).
THEMES: Tuple[Tuple[str, str, str, str, Tuple[str, ...]], ...] = (
    ("tendance", "suivi de tendance", "stratégie : cassure de 30 jours, stop suiveur",
     "ce résultat sur le suivi de tendance tient-il sur les 21 cryptos du bot, hors échantillon ?",
     ("trend following", "trend-following", "time-series momentum", "time series momentum", "breakout",
      "moving average", "trend")),
    ("momentum", "momentum", "classement des achats (momentum de 90 jours)",
     "ce momentum classe-t-il mieux les achats que celui du bot, sur 2018-2022 puis depuis 2023 ?",
     ("momentum", "cross-sectional", "relative strength")),
    ("risque", "volatilité et taille des positions", "taille des positions, stops, risque cumulé",
     "cette façon de dimensionner ou de couper les pertes réduit-elle la baisse maximale sans coûter le rendement ?",
     ("volatility targeting", "volatility scaling", "position sizing", "kelly", "risk parity", "drawdown",
      "expected shortfall", "value at risk", "stop-loss", "stop loss", "volatility")),
    ("regime", "régimes de marché", "filtre de régime (BTC au-dessus de sa moyenne 150 jours)",
     "ce repérage des régimes aurait-il évité des achats perdants du bot, sans manquer les tendances ?",
     ("regime", "hidden markov", "bull market", "bear market", "market state", "structural break")),
    ("crypto", "cryptos", "univers : 21 cryptos de Binance",
     "ce constat sur les cryptos se retrouve-t-il sur les paires du bot ?",
     ("crypto", "bitcoin", "ethereum", "stablecoin", "defi", "altcoin", "digital asset", "blockchain")),
    ("couts", "coûts d'exécution", "exécution : frais, glissement, carnet d'ordres",
     "ces coûts mesurés changent-ils le résultat du bot (research/couts.py) ?",
     ("transaction cost", "slippage", "market impact", "order book", "limit order", "liquidity", "bid-ask")),
    ("sentiment", "sentiment et actualités", "noyau de savoir (sources jugées sur les cours)",
     "cette source d'information voit-elle juste plus souvent que le hasard sur les cryptos du bot ?",
     ("sentiment", "news", "social media", "twitter", "reddit", "large language model", "llm", "attention")),
    ("methode", "pièges des backtests", "méthode des études (paramètres gelés, hors échantillon)",
     "les études du bot se protègent-elles déjà de ce biais ?",
     ("overfitting", "multiple testing", "data snooping", "deflated sharpe", "look-ahead", "backtest",
      "out-of-sample", "selection bias", "p-hacking")),
)
LABEL = {t[0]: t[1] for t in THEMES}


def _text(node: Optional[ET.Element]) -> str:
    return re.sub(r"\s+", " ", (node.text or "") if node is not None else "").strip()


def parse(raw: bytes) -> List[Dict[str, Any]]:
    """Travaux d'un flux Atom d'arXiv : identifiant, titre, résumé, auteurs,
    date, lien, catégories. Une entrée incomplète est ignorée."""
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(f"{ATOM}entry"):
        ident, title = _text(e.find(f"{ATOM}id")).rsplit("/abs/", 1)[-1], _text(e.find(f"{ATOM}title"))
        if not title or not ARXIV_ID.match(ident):
            continue
        # Lien refait depuis l'identifiant vérifié : jamais une adresse lue
        # telle quelle dans le flux.
        out.append({"id": ident, "title": title, "link": ABS + ident,
                    "summary": _text(e.find(f"{ATOM}summary")),
                    "authors": [_text(a.find(f"{ATOM}name")) for a in e.findall(f"{ATOM}author")][:6],
                    "published": _text(e.find(f"{ATOM}published"))[:10],
                    "categories": [c.get("term", "") for c in e.findall(f"{ATOM}category")]})
    return out


def _hits(text: str, words: Tuple[str, ...]) -> bool:
    """Un des mots-clés, en mot entier, au singulier ou au pluriel."""
    return any(re.search(r"\b" + re.escape(w) + r"(s|es)?\b", text) for w in words)


def relevance(paper: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Pertinence pour le bot : 2 points par thème cité dans le titre, 1 par
    thème cité dans le résumé seulement ; les cryptos et la tendance
    comptent double (le cœur du bot). Rend (score, thèmes)."""
    title, summary = paper.get("title", "").lower(), paper.get("summary", "").lower()
    score, themes = 0, []
    for key, _label, _part, _q, words in THEMES:
        points = 2 if _hits(title, words) else 1 if _hits(summary, words) else 0
        if points:
            themes.append(key)
            score += points * (2 if key in ("crypto", "tendance") else 1)
    return score, themes


def due(book: Any, now: datetime) -> bool:
    """À relire : une fois par jour, six heures après une panne."""
    last = (book or {}).get("tried_at") if isinstance(book, dict) else None
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now - when >= timedelta(hours=RETRY_HOURS if book.get("error") else REFRESH_HOURS)


def refresh(memory: Any, now: datetime, fetch: Callable[[str], bytes], force: bool = False) -> Dict[str, Any]:
    """Lit les nouveaux travaux s'il le faut et garde les pertinents dans la
    bibliothèque (méta « bibliotheque » du noyau de savoir). Une panne est
    notée, jamais bloquante ; la bibliothèque déjà lue est gardée."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        found = parse(fetch(FEED))
    except Exception as e:
        book["error"] = friendly_error(e)
        memory.put(KEY, book)
        return book
    papers = {p["id"]: p for p in book.get("papers") or []}
    new = []
    for p in found:
        score, themes = relevance(p)
        if score < MIN_SCORE or p["id"] in papers:
            continue
        p.update(score=score, themes=themes, added=now.date().isoformat(), summary=p["summary"][:600])
        papers[p["id"]] = p
        new.append(p["id"])
    kept = sorted(papers.values(), key=lambda p: (p.get("published", ""), p["score"]), reverse=True)[:KEEP]
    book.update(fetched_at=book["tried_at"], error="", read=len(found), new=new, papers=kept)
    memory.put(KEY, book)
    return book


def pistes(book: Any, n: int = SHOWN, days: int = 90) -> List[Dict[str, Any]]:
    """Les travaux les plus pertinents des `days` derniers jours, chacun avec
    la partie du bot qu'il concerne et la question à vérifier."""
    if not isinstance(book, dict):
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    recent = [p for p in book.get("papers") or [] if p.get("published", "") >= since]
    recent.sort(key=lambda p: (p.get("score", 0), p.get("published", "")), reverse=True)
    out = []
    for p in recent[:n]:
        main = p["themes"][0] if p.get("themes") else "methode"
        theme = next(t for t in THEMES if t[0] == main)
        out.append({"id": p["id"], "title": p["title"], "published": p.get("published"), "link": p.get("link"),
                    "authors": p.get("authors") or [], "score": p.get("score", 0),
                    "themes": [LABEL[t] for t in p.get("themes", [])], "part": theme[2], "question": theme[3]})
    return out


def render(book: Any) -> str:
    """Texte de la bibliothèque : état de la dernière lecture et pistes."""
    if not isinstance(book, dict) or not book.get("tried_at"):
        return "Bibliothèque de recherche : pas encore lue."
    lines = [f"Bibliothèque de recherche : {len(book.get('papers') or [])} travaux gardés "
             f"(dernière lecture : {book.get('fetched_at') or 'aucune réussie'}"
             f"{', ' + str(len(book.get('new') or [])) + ' nouveaux' if book.get('fetched_at') else ''})."]
    if book.get("error"):
        lines.append(f"Dernière lecture ratée : {book['error']} (nouvel essai dans {RETRY_HOURS} h).")
    rows = pistes(book)
    if rows:
        lines.append("Pistes à vérifier (jamais appliquées sans étude hors échantillon) :")
    for p in rows:
        lines.append(f"- {p['title']} ({p['published']}, {', '.join(p['themes'])}) : {p['part']} — "
                     f"{p['question']} {p['link']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande : pistes (défaut) ou nouvelle lecture d'arXiv."""
    import v29

    from . import savoir
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Bibliothèque de recherche de TrendGuard (arXiv q-fin)")
    ap.add_argument("action", nargs="?", default="pistes", choices=["pistes", "lire"])
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    memory = savoir.Memory(gcfg.savoir_db or savoir.DEFAULT_DB)
    try:
        book = (refresh(memory, datetime.now(timezone.utc), lambda url: savoir.http_get(url, TIMEOUT), force=True)
                if args.action == "lire" else memory.get(KEY))
        print(render(book))
        return 0
    finally:
        memory.close()
