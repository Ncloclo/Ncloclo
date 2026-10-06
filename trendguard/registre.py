"""
Registre des expériences et carte du modèle (prompt maître §41-42 et §50,
docs/PLATEFORME.md) : chaque expérience du bot est notée pour être refaite à
l'identique.

Une expérience : un rejeu de l'historique Binance avec des réglages donnés
(épreuves de l'évolution encadrée, contrôle demandé). Le registre garde, pour
chacune : son numéro, la date, la version du code (git), l'empreinte des
données utilisées, les réglages, les résultats et la conclusion. Fichier
<bot>.registre.json à côté du bot, jamais publié.

    python trendguard_bot.py registre                  # les dernières expériences
    python trendguard_bot.py registre voir <numéro>
    python trendguard_bot.py registre rejouer <numéro> # refaite : mêmes résultats ?
    python trendguard_bot.py registre controle         # rejeu des réglages en vigueur, noté
    python trendguard_bot.py registre carte            # la carte du modèle
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import v29

from . import autonomy
from . import trend_strategy as ts
from .texte import fr

KEEP = 2000                     # expériences gardées (plusieurs années)
PERIODS = (("2018-01-01", "2022-12-31"), ("2023-01-01", None))
PERIOD_LABELS = ("2018-2022", "depuis 2023")
COMPARED = ("cagr_pct", "max_dd_pct", "calmar", "trades")
TOLERANCE = 1e-6
LIMITS = ("achats seuls sur Binance Spot : ni vente à découvert, ni levier",
          "perd de petites sommes dans les marchés sans tendance (faux départs, normaux)",
          "dépend du bitcoin : aucun achat quand il est sous sa moyenne de 150 jours",
          "un krach brutal peut sauter les stops (voir les tests de résistance)",
          "bougies d'une seule plateforme (Binance) ; résultats passés, sans garantie pour l'avenir")


def registry_path(gcfg: Any) -> str:
    """Fichier du registre, à côté du verrou du bot ("" sans verrou)."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".registre.json")


def git_version(root: str = v29.APP_DIR) -> str:
    """Version du code (empreinte git courte, suivie de « -dirty » si des
    fichiers suivis ont été modifiés depuis), « inconnue » hors d'un dépôt."""
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=7"], cwd=root,
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "inconnue"
    return out.stdout.strip() or "inconnue"


def fingerprint(close: pd.DataFrame, volume: Optional[pd.DataFrame], end: str) -> Dict[str, Any]:
    """Empreinte des données jusqu'à `end` (inclus) : les indicateurs d'un
    jour ne dépendent que des jours passés, les jours suivants ne changent
    donc rien au rejeu."""
    upto = pd.Timestamp(end, tz="UTC")
    c = close.loc[:upto]
    h = hashlib.sha256(pd.util.hash_pandas_object(c, index=True).values.tobytes())
    if volume is not None:
        h.update(pd.util.hash_pandas_object(volume.loc[:upto], index=True).values.tobytes())
    return {"first": str(c.index[0].date()), "last": str(c.index[-1].date()), "assets": sorted(c.columns),
            "hash": h.hexdigest()[:16]}


def _plain(x: Any) -> Any:
    """Valeurs écrivables en JSON (nombres numpy, tuples)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "item"):
        return x.item()
    return x


def _tuples(x: Any) -> Any:
    return tuple(_tuples(v) for v in x) if isinstance(x, list) else x


def params_of(d: Dict[str, Any]) -> ts.TrendParams:
    """Réglages relus du registre (les listes JSON redeviennent des tuples)."""
    names = {f.name for f in dataclasses.fields(ts.TrendParams)}
    return ts.TrendParams(**{k: _tuples(v) for k, v in d.items() if k in names})


def experiment(kind: str, close: pd.DataFrame, volume: Optional[pd.DataFrame],
               periods: Sequence[Tuple[str, str]], params: Dict[str, ts.TrendParams],
               metrics: Dict[str, List[Dict[str, Any]]], conclusion: str,
               details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Une expérience prête à noter : réglages, résultats par période,
    empreinte des données, version du code."""
    return _plain({"kind": kind, "periods": [list(p) for p in periods],
                   "data": fingerprint(close, volume, periods[-1][1]),
                   "params": {k: dataclasses.asdict(p) for k, p in params.items()},
                   "metrics": {k: [{m: r.get(m) for m in COMPARED} for r in rows] for k, rows in metrics.items()},
                   "conclusion": conclusion, "details": details or {}})


def record(path: str, entry: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ajoute l'expérience au registre (numéro, date, version du code) et la
    renvoie ; sans fichier (tests, rejeu), elle n'est que numérotée."""
    now = now or datetime.now(timezone.utc)
    data = autonomy.read_json(path) if path else {}
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    n = int(data.get("next") or len(entries) + 1)
    entry = dict(entry, id=f"E{n:04d}", at=now.isoformat(timespec="seconds"),
                 code=entry.get("code") or git_version())
    if path:
        autonomy.write_json(path, {"next": n + 1, "entries": (entries + [entry])[-KEEP:]})
    return entry


def load(path: str) -> List[Dict[str, Any]]:
    """Les expériences notées, de la plus ancienne à la plus récente."""
    entries = autonomy.read_json(path).get("entries") if path else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def find(path: str, ident: str) -> Optional[Dict[str, Any]]:
    """Une expérience par son numéro (E0012, 12 ou e12)."""
    ident = ident.strip().upper().lstrip("E")
    for e in load(path):
        if str(e.get("id", "")).upper().lstrip("E").lstrip("0") == ident.lstrip("0"):
            return e
    return None


def run_metrics(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams,
                periods: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Résultats d'un réglage sur chaque période (la boucle de backtest du
    bot, mêmes frais)."""
    pre = ts.precompute(close, volume, p)
    return [ts.backtest(close, volume, p, a, b, pre=pre).metrics for a, b in periods]


def replay(entry: Dict[str, Any], close: pd.DataFrame, volume: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """L'expérience refaite : mêmes données (empreinte), mêmes résultats ?"""
    periods = [tuple(p) for p in entry.get("periods") or []]
    data = fingerprint(close, volume, periods[-1][1]) if periods else {}
    diffs: List[str] = []
    for name, d in (entry.get("params") or {}).items():
        got = run_metrics(close, volume, params_of(d), periods)
        for i, (old, new) in enumerate(zip(entry["metrics"].get(name) or [], got)):
            for m in COMPARED:
                a, b = old.get(m), new.get(m)
                if a is None or b is None:
                    continue
                if abs(float(a) - float(b)) > TOLERANCE * max(1.0, abs(float(a))):
                    diffs.append(f"{name}, {PERIOD_LABELS[i] if i < len(PERIOD_LABELS) else i} : {m} "
                                 f"{fr(float(a), '.4g')} → {fr(float(b), '.4g')}")
    same_data = data.get("hash") == (entry.get("data") or {}).get("hash")
    return {"same": not diffs, "same_data": same_data, "diffs": diffs, "data": data}


def _periods(close: pd.DataFrame) -> List[Tuple[str, str]]:
    last = str(close.index[-1].date())
    return [(a, b or last) for a, b in PERIODS]


def control(gcfg: Any, close: pd.DataFrame, volume: Optional[pd.DataFrame],
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Rejeu des réglages en vigueur (évolution comprise) sur les deux
    époques, noté au registre : le point de référence de la carte du modèle."""
    from . import evolution
    p = evolution.params_for(gcfg)
    periods = _periods(close)
    rows = run_metrics(close, volume, p, periods)
    text = " ; ".join(f"{lab} : {fr(r['cagr_pct'], '+.1f')} %/an, pire baisse {fr(r['max_dd_pct'], '.1f')} %, "
                      f"Calmar {fr(r['calmar'], '.2f')}" for lab, r in zip(PERIOD_LABELS, rows))
    return record(registry_path(gcfg), experiment("controle", close, volume, periods, {"en vigueur": p},
                                                  {"en vigueur": rows}, f"Réglages en vigueur : {text}"), now)


def describe(e: Dict[str, Any]) -> str:
    """Une ligne par expérience."""
    data = e.get("data") or {}
    return (f"{e.get('id')} · {str(e.get('at', ''))[:16].replace('T', ' ')} · {e.get('kind')} · code "
            f"{e.get('code')} · données {data.get('first')} → {data.get('last')} ({data.get('hash')}) · "
            f"{e.get('conclusion', '')}")


def card(gcfg: Any, entries: Sequence[Dict[str, Any]]) -> str:
    """La carte du modèle : à quoi il sert, ses règles en vigueur, sa
    validation (dernière expérience notée), ses limites et ses garde-fous."""
    from . import evolution
    p = evolution.params_for(gcfg)
    lines = [f"CARTE DU MODÈLE — TrendGuard (code {git_version()}, mode {gcfg.run_mode})", "",
             "Usage : suivi de tendance sur Binance Spot, bougies journalières, "
             f"{len(gcfg.universe)} cryptos ({', '.join(b.upper() for b in gcfg.universe)}).",
             f"Règles en vigueur : achat à la cassure du plus haut de {p.breakout_n} jours, cryptos classées "
             f"par leur hausse sur {p.mom_n} jours ; seulement si le bitcoin est au-dessus de sa moyenne de "
             f"{p.regime_sma} jours ; stop initial {fr(p.init_stop_atr, 'g')} × la volatilité, stop suiveur "
             f"{fr(p.trail_atr, 'g')} × la volatilité ({fr(p.bear_trail_atr, 'g')} × en marché baissier).",
             f"Risque : {fr(p.risk_pct * 100, 'g')} % du capital par achat, {fr(p.max_total_risk * 100, 'g')} % "
             f"au plus en cumulé, {p.max_positions} positions au plus ; arrêt d'urgence à "
             f"−{fr(gcfg.kill_drawdown * 100, '.0f')} % depuis le plus haut ; garde « NO TRADE ».", ""]
    ref = next((e for e in reversed(list(entries)) if e.get("kind") in ("controle", "evolution")), None)
    if ref:
        lines.append(f"Validation (expérience {ref['id']} du {str(ref.get('at', ''))[:10]}, données "
                     f"{(ref.get('data') or {}).get('first')} → {(ref.get('data') or {}).get('last')}) :")
        name = next(iter(ref.get("metrics") or {}), "")
        for lab, r in zip(PERIOD_LABELS, (ref.get("metrics") or {}).get(name) or []):
            lines.append(f"  {lab} : {fr(float(r['cagr_pct']), '+.1f')} %/an, pire baisse "
                         f"{fr(float(r['max_dd_pct']), '.1f')} %, Calmar {fr(float(r['calmar']), '.2f')}, "
                         f"{r['trades']} trades")
    else:
        lines.append("Validation : aucune expérience notée ; python trendguard_bot.py registre controle")
    lines += ["", "Limites connues :"] + [f"  - {x}" for x in LIMITS]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande du registre : liste (défaut), voir, rejouer,
    controle, carte."""
    from .config import load_guard_config_from_env
    from .evolution import load_history
    ap = argparse.ArgumentParser(description="Registre des expériences de TrendGuard")
    ap.add_argument("action", nargs="?", default="liste", choices=["liste", "voir", "rejouer", "controle", "carte"])
    ap.add_argument("numero", nargs="?", default="", help="(voir, rejouer) numéro de l'expérience, ex. E0012")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    path = registry_path(gcfg)
    if args.action == "liste":
        entries = load(path)
        print(f"{len(entries)} expérience(s) notée(s)" + (" ; les 20 dernières :" if len(entries) > 20 else ""))
        for e in entries[-20:]:
            print(describe(e))
        return 0
    if args.action == "carte":
        print(card(gcfg, load(path)))
        return 0
    if args.action == "controle":
        e = control(gcfg, *load_history(args.cache, list(gcfg.universe)))
        print(describe(e))
        return 0
    e = find(path, args.numero) if args.numero else None
    if e is None:
        print(f"Expérience introuvable : {args.numero or '(numéro manquant)'}")
        return 1
    if args.action == "voir":
        print(describe(e))
        for name, rows in (e.get("metrics") or {}).items():
            for lab, r in zip(PERIOD_LABELS, rows):
                print(f"  {name}, {lab} : {r}")
        return 0
    res = replay(e, *load_history(args.cache, list(gcfg.universe)))
    print(describe(e))
    print("Données : " + ("identiques" if res["same_data"] else
                          f"différentes (empreinte {res['data'].get('hash')}) : Binance a corrigé des bougies"))
    print("Résultats : " + ("identiques, l'expérience est reproductible" if res["same"]
                            else "différents :\n  " + "\n  ".join(res["diffs"])))
    return 0 if res["same"] else 1
