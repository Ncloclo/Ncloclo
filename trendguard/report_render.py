"""Rapport quotidien, mise en page (docs/RAPPORT.md) : texte complet
(panneau, e-mail sans page, archive), résumé court (WhatsApp, Telegram) et
page mise en forme de l'e-mail. Aucun secret n'y figure.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from .systeme import Check

SHORT_MAX = 900                 # résumé WhatsApp


def _icon(ok: Optional[bool]) -> str:
    return "✓" if ok is True else "✗" if ok is False else "i"


def render_text(r: Dict[str, Any]) -> str:
    s = r["score"]
    when = r["generated_at"][:16].replace("T", " à ")
    lines = [f"RAPPORT QUOTIDIEN TRENDGUARD — {r['day']} ({when} UTC, mode {r['mode']})",
             "",
             f"Verdict : {r['verdict']}. {s['ok']} contrôle(s) conforme(s), {s['warn']} à corriger, "
             f"{s['info']} information(s), sur {s['total']}.", ""]
    lines += ["CE QUE LE BOT A FAIT SEUL"] + ([f"- {a}" for a in r["actions"]] or
                                               ["- rien à corriger automatiquement"]) + [""]
    lines += ["À FAIRE (par ordre d'importance)"] + ([f"{k}. {x}" for k, x in enumerate(r["recommendations"], 1)]
                                                     or ["- rien : tout est en ordre"]) + [""]
    for sec in r["sections"]:
        lines.append(sec["title"].upper())
        lines += [f"  {_icon(c['ok'])} {c['label']} : {c['detail']}" for c in sec["checks"]]
        lines.append("")
    if r["proposals"]:
        lines += ["PROPOSITIONS D'AMÉLIORATION EN ATTENTE DE VOTRE VALIDATION (GitHub)"]
        lines += [f"- {p}" for p in r["proposals"]] + [""]
    lines += ["Règles de sagesse : le bot applique seul les protections sûres et réversibles "
              "(sauvegarde, droits du fichier des secrets, secrets masqués dans les journaux). "
              "Il ne touche jamais aux règles, au risque, aux clés, au mode réel, au code ni aux "
              "réglages de Windows : ces points restent des recommandations.",
              "Rapport complet : panneau ▸ Réglages ▸ Rapport quotidien. Aucun secret n'y figure."]
    return "\n".join(lines)


def render_short(r: Dict[str, Any]) -> str:
    s = r["score"]
    head = (f"🛡️ TrendGuard, rapport du {r['day']} : {r['verdict'].lower()} "
            f"({s['ok']}/{s['total']} ✓).")
    todo = [f"{k}. {x}" for k, x in enumerate(r["recommendations"][:3], 1)]
    tail = "Rapport complet : panneau ▸ Réglages ▸ Rapport quotidien (et par e-mail)."
    text = "\n".join([head] + todo + [tail])
    return text if len(text) <= SHORT_MAX else text[:SHORT_MAX - 1] + "…"


_COLORS = {True: ("#15803d", "#dcfce7", "✓"), False: ("#b91c1c", "#fee2e2", "!"),
           None: ("#1d4ed8", "#dbeafe", "i")}


def render_html(r: Dict[str, Any]) -> str:
    """Le rapport complet en page e-mail : styles en ligne (seuls lus par les
    messageries), une colonne lisible sur téléphone, tout texte échappé."""
    esc = html.escape
    s = r["score"]
    bad = s["warn"] > 0
    when = r["generated_at"][:16].replace("T", " à ")
    head_color = "#b91c1c" if bad else "#15803d"

    def items(values: List[str], ordered: bool) -> str:
        tag = "ol" if ordered else "ul"
        return (f"<{tag} style='margin:6px 0 0;padding-left:22px'>"
                + "".join(f"<li style='margin:4px 0'>{esc(v)}</li>" for v in values) + f"</{tag}>")

    def row(c: Check) -> str:
        fg, bg, icon = _COLORS[c["ok"]]
        return ("<tr><td style='width:26px;vertical-align:top;padding:6px 0'>"
                f"<span style='display:inline-block;width:20px;height:20px;border-radius:10px;"
                f"background:{bg};color:{fg};font-weight:700;text-align:center;line-height:20px;"
                f"font-size:12px'>{icon}</span></td><td style='padding:6px 0;font-size:14px'>"
                f"<b>{esc(c['label'])}</b> <span style='color:#475569'>· {esc(c['detail'])}</span>"
                "</td></tr>")

    parts = [
        "<div style='font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;padding:16px'>",
        "<div style='max-width:680px;margin:0 auto;background:#ffffff;border-radius:14px;"
        "overflow:hidden;border:1px solid #e2e8f0'>",
        f"<div style='background:{head_color};color:#ffffff;padding:18px 22px'>"
        f"<div style='font-size:13px;opacity:.9'>TrendGuard · rapport quotidien du {esc(r['day'])}</div>"
        f"<div style='font-size:22px;font-weight:700;margin-top:4px'>{esc(r['verdict'])}</div>"
        f"<div style='font-size:13px;margin-top:4px'>{s['ok']} contrôles conformes sur {s['total']} · "
        f"{esc(when)} UTC · mode {esc(r['mode'])}</div></div>",
        "<div style='padding:6px 22px 20px;color:#0f172a'>",
        "<h3 style='margin:18px 0 0;font-size:15px'>À faire, par ordre d'importance</h3>",
        items(r["recommendations"] or ["Rien : tout est en ordre."], True),
        "<h3 style='margin:18px 0 0;font-size:15px'>Ce que le bot a fait seul</h3>",
        items(r["actions"] or ["Rien à corriger automatiquement."], False),
    ]
    for sec in r["sections"]:
        parts.append(f"<h3 style='margin:20px 0 4px;font-size:15px;border-top:1px solid #e2e8f0;"
                     f"padding-top:14px'>{esc(sec['title'])}</h3><table style='width:100%;"
                     "border-collapse:collapse'>" + "".join(row(c) for c in sec["checks"]) + "</table>")
    if r["proposals"]:
        parts.append("<h3 style='margin:20px 0 0;font-size:15px'>Améliorations à valider sur GitHub</h3>"
                     + items(r["proposals"], False))
    parts += ["<p style='margin:20px 0 0;font-size:12px;color:#64748b'>Le bot applique seul les "
              "protections sûres et réversibles, et installe seul les améliorations que vous avez "
              "validées. Il ne touche jamais aux règles, au risque, aux clés ni au mode réel. Aucun "
              "secret ne figure dans ce rapport. Rapport complet : panneau ▸ Réglages ▸ Rapport "
              "quotidien.</p>", "</div></div></div>"]
    return "".join(parts)
