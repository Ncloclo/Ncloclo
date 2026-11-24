"""Bibliothèque de recherche (trendguard/bibliotheque.py) : travaux d'arXiv
rangés par thème et reliés aux règles du bot ; des pistes, jamais des
règles ; un flux lu est une donnée (lien refait, jamais repris tel quel)."""

from datetime import datetime, timezone

from trendguard import bibliotheque as bib
from trendguard import cli, cyber, savoir

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _entry(ident, title, summary, published="2026-11-20T10:00:00Z", link="http://arxiv.org/abs/x"):
    return (f"<entry><id>http://arxiv.org/abs/{ident}</id><title>{title}</title><summary>{summary}</summary>"
            f"<published>{published}</published><author><name>A. Chercheur</name></author>"
            f'<link href="{link}" rel="alternate" type="text/html"/>'
            f'<category term="q-fin.TR"/></entry>')


def _feed(*entries):
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            + "".join(entries) + "</feed>").encode()


MOMENTUM = _entry("2611.01234v1", "Time-series momentum in cryptocurrency markets",
                  "We study trend following on bitcoin with volatility targeting and transaction costs.")
OPTIONS = _entry("2611.05678v2", "Pricing exotic options with rough volatility",
                 "A stochastic calculus approach.")
TRAP = _entry("../../etc/passwd", "Momentum crypto trend", "momentum crypto trend",
              link="javascript:alert(1)")


def test_parse_keeps_valid_entries_and_rebuilds_links():
    papers = bib.parse(_feed(MOMENTUM, OPTIONS, TRAP))
    assert [p["id"] for p in papers] == ["2611.01234v1", "2611.05678v2"]        # identifiant piégé ignoré
    assert papers[0]["link"] == "https://arxiv.org/abs/2611.01234v1"              # jamais le lien du flux
    assert papers[0]["published"] == "2026-11-20" and papers[0]["authors"] == ["A. Chercheur"]


def test_relevance_ties_a_paper_to_the_bot_rules():
    momentum, options = bib.parse(_feed(MOMENTUM, OPTIONS))
    score, themes = bib.relevance(momentum)
    assert {"tendance", "momentum", "crypto", "risque", "couts"} <= set(themes) and score >= bib.MIN_SCORE
    assert bib.relevance(options)[0] < bib.MIN_SCORE                            # sans rapport : écarté


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = value


def test_refresh_keeps_relevant_papers_once_and_survives_a_failure():
    memory, calls = _Memory(), []

    def fetch(url):
        calls.append(url)
        return _feed(MOMENTUM, OPTIONS)
    book = bib.refresh(memory, NOW, fetch)
    assert calls == [bib.FEED] and book["new"] == ["2611.01234v1"] and len(book["papers"]) == 1
    assert bib.refresh(memory, NOW, fetch) is not None and len(calls) == 1      # une fois par jour
    later = NOW.replace(day=25, hour=13)
    book = bib.refresh(memory, later, fetch)
    assert book["new"] == [] and len(book["papers"]) == 1                       # pas de doublon

    def down(url):
        raise TimeoutError("timed out")
    book = bib.refresh(memory, NOW.replace(day=27), down)
    assert book["error"] == "délai dépassé" and len(book["papers"]) == 1        # rien de perdu
    assert not bib.due(book, NOW.replace(day=27, hour=17)) and bib.due(book, NOW.replace(day=27, hour=19))


def test_pistes_say_which_rule_and_which_question():
    memory = _Memory()
    bib.refresh(memory, datetime.now(timezone.utc), lambda url: _feed(MOMENTUM.replace("2026-11-20", datetime.now(
        timezone.utc).date().isoformat())))
    rows = bib.pistes(memory.get(bib.KEY))
    assert len(rows) == 1 and rows[0]["part"].startswith("stratégie") and rows[0]["question"].endswith("?")
    text = bib.render(memory.get(bib.KEY))
    assert "jamais appliquées sans étude hors échantillon" in text and "https://arxiv.org/abs/" in text


def test_the_library_is_a_bot_tool_and_its_host_is_declared():
    assert "bibliotheque" in cli.TOOLS
    assert "export.arxiv.org" in cyber.ALLOWLIST


def test_a_refused_feed_is_described_honestly():
    """Un flux qui répond 404 (Reddit sans compte) n'est pas présenté comme
    une erreur de modèle d'IA."""
    from trendguard import market_watch as mw
    assert "refuse les lectures sans compte" in savoir.why_failed(mw.HttpError(404, ""))
    assert "limite les lectures" in savoir.why_failed(mw.HttpError(429, ""))


def test_the_researcher_agent_informs_the_committee_without_voting():
    """Agent « chercheur » du comité : il cite les travaux récents qui
    touchent à l'achat, avec la question à vérifier ; poids nul (il ne
    vote pas) et une incertitude rappelée."""
    from trendguard import comite
    agent = next(a for a in comite.AGENTS if a.manifest.agent_id == "chercheur")
    assert agent.manifest.weight == 0.0 and agent.manifest.reads == ("savoir",)
    piste = {"title": "Time-series momentum in crypto", "part": "stratégie : cassure de 30 jours, stop suiveur",
             "question": "ce résultat tient-il hors échantillon ?", "link": "https://arxiv.org/abs/2611.01234v1"}
    r = comite.a_chercheur({"savoir": {"pistes": [piste]}})
    assert r.stance == "NEUTRE" and r.score == 0 and "Time-series momentum in crypto" in r.evidence[0]
    assert r.uncertainties and "pas une preuve" in r.uncertainties[0]
    assert comite.a_chercheur({"savoir": {}}).evidence == ("aucun travail de recherche récent sur ces règles",)
