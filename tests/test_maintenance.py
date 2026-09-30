"""Maintenance autonome (trendguard/maintenance.py) : corrections sûres et
réversibles appliquées seules (veille du PC sur secteur), mises à jour
installées seulement si le propriétaire les a validées (Pull Request qu'il a
fusionnée) et si les contrôles GitHub sont au vert, avec retour automatique
en arrière ; redémarrage prévu du bot par son superviseur."""

import logging
import os
import time
from types import SimpleNamespace

import pytest
from test_autonomy import Clock, _supervisor

import trendguard_bot as tg
import v29
from trendguard import autonomy
from trendguard import maintenance as mt
from trendguard import report as rp
from trendguard.systeme import Deps, chk


def proc(stdout="", code=0, stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class Power:
    """powercfg simulé : réglages sur secteur en secondes (veille) ou index (capot)."""

    def __init__(self, stuck=False, **values):
        self.v = dict(values)
        self.stuck = stuck
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["powercfg", "/query"]:
            key = cmd[-1]
            if key not in self.v:
                return proc("GUID du mode : (Utilisation normale)\n")
            return proc(f"Minimum 0x00000000\nMaximum 0xffffffff\nAC 0x{self.v[key]:08x}\nDC 0x00000384")
        if self.stuck:
            return proc("")
        if cmd[:2] == ["powercfg", "/change"]:
            key = {"standby-timeout-ac": "STANDBYIDLE", "hibernate-timeout-ac": "HIBERNATEIDLE"}[cmd[2]]
            self.v[key] = int(cmd[3]) * 60
        if cmd[:2] == ["powercfg", "/setacvalueindex"]:
            self.v[cmd[4]] = int(cmd[5])
        return proc("")


def _g(tmp_path, **kw):
    return tg.GuardConfig(db_file=":memory:", log_file=os.devnull,
                          lock_file=str(tmp_path / "tg.lock"), **kw)


def test_power_is_fixed_on_mains_only_and_can_be_restored(tmp_path):
    g = _g(tmp_path)
    pw = Power(STANDBYIDLE=900, HIBERNATEIDLE=10800, LIDACTION=1)
    c = mt.fix_power(g, Deps(run=pw, platform="win32"))
    assert c["ok"] is True and pw.v == {"STANDBYIDLE": 0, "HIBERNATEIDLE": 0, "LIDACTION": 0}
    assert "mise en veille sur secteur : jamais (avant : après 15 min)" in c["action"]
    assert "capot fermé sur secteur : ne rien faire (avant : veille)" in c["action"]
    assert mt.fix_power(g, Deps(run=pw, platform="win32")) is None          # plus rien à faire
    changes = [c for c in pw.calls if c[1] != "/query"]
    assert changes and not any("-dc" in " ".join(c) or "setdcvalueindex" in c for c in changes)  # batterie : jamais touchée
    text = mt.restore_power(g, Deps(run=pw, platform="win32"))
    assert pw.v == {"STANDBYIDLE": 900, "HIBERNATEIDLE": 10800, "LIDACTION": 1}
    assert "d'origine remis" in text
    off = mt.fix_power(g, Deps(run=pw, platform="win32"))
    assert off["ok"] is None and "arrêtée à votre demande" in off["detail"] and pw.v["STANDBYIDLE"] == 900
    mt.resume_fixes(g)
    assert mt.fix_power(g, Deps(run=pw, platform="win32"))["ok"] is True
    assert mt.fix_power(g, Deps(run=pw, platform="linux")) is None


def test_power_fix_refused_by_windows_is_reported(tmp_path):
    pw = Power(stuck=True, STANDBYIDLE=900)                                  # droits insuffisants
    c = mt.fix_power(_g(tmp_path), Deps(run=pw, platform="win32"))
    assert c["ok"] is False and "mise en veille" in c["detail"] and "Alimentation" in c["reco"]


class Git:
    """Dépôt git simulé : historique local et publié."""

    def __init__(self, new=("bbb2222", "ccc3333"), dirty="", fetch_ok=True, ff=True):
        self.new, self.dirty, self.fetch_ok, self.ff, self.calls = list(new), dirty, fetch_ok, ff, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1:])
        a = cmd[1:]
        table = {("status",): self.dirty, ("rev-parse", "--abbrev-ref"): "claude/main\n",
                 ("remote", "get-url"): "https://github.com/Ncloclo/Ncloclo.git\n",
                 ("rev-parse", "HEAD"): "aaa1111\n", ("rev-list",): "\n".join(self.new)}
        if a[0] == "fetch":
            return proc("", 0 if self.fetch_ok else 1)
        if a[0] == "merge-base":
            return proc("", 0 if self.ff else 1)
        if a[0] in ("merge", "reset"):
            return proc("")
        for k, v in table.items():
            if tuple(a[:len(k)]) == k:
                return proc(v)
        return proc("", 1)


def gh_factory(ci="success", merger="Ncloclo", merged=True):
    def gh(url):
        if "actions/runs" in url:
            if ci is None:
                return {"workflow_runs": []}
            return {"workflow_runs": [{"name": "Contrôles", "status": "completed", "conclusion": ci}]}
        if "/commits/" in url:
            return [{"number": 7, "merged_at": "2026-09-30T00:10:00Z" if merged else None}]
        if url.endswith("/pulls/7"):
            return {"merged_by": {"login": merger}, "title": "Amélioration quotidienne : contrastes"}
        return []
    return gh


def _update(tmp_path, git, gh, mode="paper", check=(True, ""), health=(True, ""), supervised=True):
    restarts = []
    g = _g(tmp_path) if mode == "paper" else _g(tmp_path, run_mode="live", enable_live_trading=True,
                                                   live_confirmation="I_UNDERSTAND_RISK")
    c = mt.update(g, Deps(run=git), str(tmp_path), gh=gh, check=lambda root, deps: check,
                  restart=lambda gc: restarts.append(gc), health=lambda gc, since, port: health,
                  supervised=lambda gc: supervised)
    return c, restarts


def test_update_installs_only_what_the_owner_validated(tmp_path):
    git = Git()
    c, restarts = _update(tmp_path, git, gh_factory())
    assert c["ok"] is True and "installé(s) et vérifié(s)" in c["detail"]
    assert "aaa1111 → ccc3333" in c["action"] and "#7 Amélioration quotidienne" in c["action"]
    assert ["merge", "--ff-only", "--quiet", "origin/claude/main"] in git.calls and len(restarts) == 1
    git = Git()
    c, restarts = _update(tmp_path, git, gh_factory(merger="claude[bot]"))
    assert c["ok"] is False and "sans votre validation" in c["detail"] and "NON installé" in c["detail"]
    assert not any(a[0] == "merge" for a in git.calls) and restarts == []
    c, _ = _update(tmp_path, Git(), gh_factory(merged=False))
    assert c["ok"] is False                                               # changement sans Pull Request


@pytest.mark.parametrize("kw,expect", [
    ({"git": Git(new=())}, "version à jour"),
    ({"git": Git(dirty=" M trendguard/bot.py\n")}, "modifiés sur ce PC"),
    ({"git": Git(fetch_ok=False)}, "GitHub injoignable"),
    ({"git": Git(ff=False)}, "ne prolonge pas"),
    ({"gh": gh_factory(ci=None)}, "contrôles GitHub en cours"),
    ({"gh": gh_factory(ci="failure")}, "contrôles GitHub en échec"),
    ({"mode": "live"}, "en mode réel"),
    ({"supervised": False}, "relance automatique inactive"),
])
def test_update_waits_or_refuses_with_wisdom(tmp_path, kw, expect):
    git = kw.pop("git", Git())
    c, restarts = _update(tmp_path, git, kw.pop("gh", gh_factory()), **kw)
    assert expect in c["detail"] and not any(a[0] == "merge" for a in git.calls) and restarts == []


def test_update_rolls_back_a_faulty_version(tmp_path):
    git = Git()
    c, restarts = _update(tmp_path, git, gh_factory(), check=(False, "chargement impossible"))
    assert c["ok"] is False and "version précédente gardée" in c["detail"]
    assert ["reset", "--hard", "--quiet", "aaa1111"] in git.calls and restarts == []
    git = Git()
    c, restarts = _update(tmp_path, git, gh_factory(), health=(False, "le bot ne reprend pas ses cycles"))
    assert c["ok"] is False and "retour automatique" in c["detail"]
    assert ["reset", "--hard", "--quiet", "aaa1111"] in git.calls and len(restarts) == 2


def test_planned_restart_is_not_a_crash(tmp_path):
    lg = logging.getLogger("test.maintenance")
    g, clock, notes = _g(tmp_path), Clock(), []
    sup, launched = _supervisor(g, lg, clock, [(5, autonomy.RESTART_CODE), (5, 0)], notes)
    assert sup.run() == 0
    assert len(launched) == 2 and sup.restarts == 0 and sup.crashes == 0 and notes == []
    assert sup.last_exit["code"] == 0


def test_bot_leaves_for_a_planned_restart(tmp_path):
    lg = logging.getLogger("test.maintenance")
    g = _g(tmp_path)
    bot = tg.TrendGuardBot(g, lg, None, v29.Store(":memory:", lg), lambda *a, **k: True)
    assert autonomy.request_restart(g)
    assert bot.stop_requested() and bot._restart_flag
    stale = tg.TrendGuardBot(g, lg, None, v29.Store(":memory:", lg), lambda *a, **k: True)
    autonomy.request_restart(g)
    old = time.time() - 3600
    os.utime(autonomy.sidecar(g.lock_file, ".restart"), (old, old))
    assert not stale.stop_requested() and not stale._restart_flag         # demande périmée


def test_report_shows_what_was_applied_first(tmp_path):
    g = _g(tmp_path)
    applied = [chk("Correction de la veille du PC", True, "appliquée",
                   action="mise en veille sur secteur : jamais (avant : après 15 min)")]
    r = rp.build(g, {}, rp.Deps(run=lambda cmd, **kw: proc("", 1), http_json=lambda url: {},
                                panel=lambda port, pw: {}, binance=lambda env, t: chk("Clé", None, "absente"),
                                diagnose=lambda gc: ([], "2026-09-29"), platform="linux",
                                extra={"root": str(tmp_path)}),
                 backups=False, applied=applied)
    assert r["sections"][0]["title"] == "Recommandations appliquées seules"
    assert r["actions"][0] == "mise en veille sur secteur : jamais (avant : après 15 min)"
