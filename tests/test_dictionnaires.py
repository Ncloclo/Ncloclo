"""Dictionnaires du panneau (panel/static/js/i18n/*.js) : chaque texte de la
page principale a sa traduction anglaise, les parties variables ({0}…) sont
gardées, et les langues écrites pour l'essentiel couvrent le menu, la page
Paiement et Rachelle."""

import html
import json
import pathlib
import re

import pytest

from trendguard import paiements

ROOT = pathlib.Path(__file__).resolve().parent.parent
I18N = ROOT / "panel" / "static" / "js" / "i18n"
CORE = ("de", "es", "pt", "ru", "zh", "ar")
# Noms propres et sigles : identiques dans toutes les langues.
SAME = {"TrendGuard", "TradingView", "Rachelle", "BRVM", "AUTO", "Bot", "Log", "15 min", "Heikin Ashi", "Crypto",
        "Finance", "Positions", "Indices", "Volume", "Auto", "Prix", "3 s", "1 s"}


def load(code):
    src = (I18N / f"{code}.js").read_text(encoding="utf-8")
    body = src[src.index("export default {") + len("export default "):src.rindex("}") + 1]
    return json.loads(re.sub(r",\s*}$", "}", body))


def page_texts():
    t = (ROOT / "panel" / "static" / "index.html").read_text(encoding="utf-8")
    body = t[t.index("<body"):]
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    body = re.sub(r"<symbol.*?</symbol>", "", body, flags=re.S)
    body = re.sub(r"<code>.*?</code>", "<br>", body, flags=re.S)        # du code : pas à traduire
    found = [re.sub(r"\s+", " ", html.unescape(m)).strip() for m in re.findall(r">([^<>]+)<", body)]
    found += [re.sub(r"\s+", " ", html.unescape(v)).strip()
              for v in re.findall(r'(?:title|placeholder|aria-label|data-title)="([^"]+)"', body)]
    return {s for s in found if re.search(r"[A-Za-zÀ-ÿ]{2}", s)}


def test_every_text_of_the_main_page_has_its_english():
    en = load("en")
    missing = sorted(s for s in page_texts() - SAME if s not in en)
    assert not missing, missing


@pytest.mark.parametrize("code", ("en",) + CORE)
def test_variable_parts_are_kept(code):
    for fr, out in load(code).items():
        assert set(re.findall(r"\{\d+\}", out)) <= set(re.findall(r"\{\d+\}", fr)), (code, fr)
        assert out.strip(), (code, fr)


@pytest.mark.parametrize("code", CORE)
def test_core_languages_cover_menu_payment_and_rachelle(code):
    d = load(code)
    menu = re.findall(r'data-tab="[a-z]+"[^>]*><svg>.*?</svg><span>([^<]+)</span>',
                      (ROOT / "panel" / "static" / "index.html").read_text(encoding="utf-8"))
    assert len(menu) >= 18 and all(m in d for m in menu if m not in SAME), [m for m in menu if m not in d]
    for fr in ("Paiement", "Payer dans votre pays", "📍 Me localiser", "Écrire à Rachelle", "Langue du panneau"):
        assert fr in d, (code, fr)
    for k in paiements.KINDS:
        assert k[1] in d, (code, k[1])                  # les familles de paiement
    for tip in paiements.SAFETY:
        assert tip[0] in d, (code, tip[0][:40])         # les conseils de sécurité
