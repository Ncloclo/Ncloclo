"""Rachelle in English (panel/assistant.py): the answers to the main topics,
written from the same data as the French ones. The panel speaks the official
language of the country of the device that views it (trendguard/profil.py) ;
for any language other than French, Rachelle answers in English and the
device translates if it can (or the configured AI answers in that language).
A topic without an English text is answered in French, saying so."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from trendguard import paiements, profil

NAME = "Rachelle"
WELCOME = f"I'm {NAME}, delighted to welcome you. How can I help you today?"
REFUSED = "🔒 I'm sorry, I can't answer this request: it concerns your security."
MASKED = ("🔒 I'm sorry, I masked your message: it seemed to contain secret information (key, password or code). "
          "It was neither sent nor saved. If it was a real key or password, I advise you to revoke it: on Binance, "
          "« API Management » → delete the key, then create a new one with masked input "
          "(python trendguard_bot.py set-keys).")
SCOPE = ("I can help you with the bot's goals, crypto and financial markets, trading, connecting and setting up "
         "your phone, payments in your country, using the panel and your access.")
NOT_UNDERSTOOD = "I'm sorry, I didn't quite understand your question. " + SCOPE + " Here are a few ideas:"
LISTENING = "I'm listening: what would you like to know?"
FRENCH_ONLY = "(I can only explain this topic in French for now.)"
OPENERS = ("With pleasure!", "Of course!", "Very good question!", "Gladly!")
CLOSERS = ("Don't hesitate if you have another question.", "I remain at your disposal.",
           "Can I help you with anything else?")
SUGGESTIONS = ["What is the bot's goal?", "What will the bot do tonight?", "How is the crypto market doing?",
               "Am I safe?", "How do I pay in my country?", "What about the stock market?", "Connect my phone",
               "How do I use the panel?", "Create my access (password)", "What is a stop?"]

RULES_DEFAULT = {"breakout_n": 30, "regime_sma": 150, "trail_atr": 5.0, "bear_trail_atr": 2.0}


def num(v: Any, d: int = 2) -> str:
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "–"


def pc(v: Any, d: int = 1) -> str:
    try:
        return f"{float(v):+.{d}f}%"
    except (TypeError, ValueError):
        return "–"


def px(v: Any) -> str:
    try:
        a = abs(float(v))
    except (TypeError, ValueError):
        return "–"
    return num(v, 0 if a >= 1000 else 2 if a >= 1 else 4 if a >= 0.1 else 5)


def hm(hours: Any) -> str:
    try:
        m = max(0, int(round(float(hours) * 60)))
    except (TypeError, ValueError):
        return "–"
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def rules(ctx: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(RULES_DEFAULT, **((ctx.get("status") or {}).get("rules") or {}))
    bear = (f"{float(r['bear_trail_atr']):g} × in a bear market" if float(r["bear_trail_atr"]) > 0
            else "the same in a bear market")
    return {"breakout_n": int(r["breakout_n"]), "regime_sma": int(r["regime_sma"]),
            "trail": f"{float(r['trail_atr']):g}", "bear": bear}


def headlines(news: Dict[str, Any], category: Optional[str], n: int) -> List[str]:
    items = [i for i in news.get("items") or [] if category is None or i.get("category") == category]
    return [f"- {i['title']} ({i['source']})" for i in items[:n]]


GLOSSARY = {
    "stop": ("**Stop**: the price level where the bot sells to limit the loss. The closing stop rises with the "
             "price ({trail} × the volatility below the high; {bear}) and is only checked at the daily close, to "
             "ignore wicks. The catastrophe stop, placed on Binance a little lower, protects against a crash "
             "between two closes."),
    "breakout": ("**Breakout**: the close goes above the highest price of the last {breakout_n} days. It is the "
                 "bot's entry signal, provided the 90-day trend is positive and the market is bullish."),
    "regime": ("**Market regime**: the bot compares bitcoin with its average of the last {regime_sma} days. "
               "Above it, a bull market: purchases allowed. Below it: no purchase, and stops are tightened to "
               "protect the gains."),
    "risk": ("**1% risk**: each purchase is sized so that, if it hits its stop, the loss is about 1% of the "
             "capital. A volatile crypto therefore gets a smaller position than a calm one. The bot may raise "
             "this risk to 2% at most, 0.25% at a time, only if its analysis justifies it, and goes back to 1% at "
             "the first warning (10% drop, bear market)."),
    "r": ("**R**: a multiple of the risk taken. +3 R = a gain equal to three times the loss planned at the start; "
          "−1 R = the planned loss."),
    "drawdown": ("**Drawdown (drop from the high)**: the gap between the current capital and its record. Beyond "
                 "−40%, the emergency stop blocks purchases; it lifts by itself after 60 days if the market has "
                 "turned bullish again (once a year), with the risk halved for 90 days."),
    "paper": ("**Paper mode**: the bot follows Binance's real prices but with fictitious money. Real mode requires "
              "keys, a double confirmation in the .env file, and a confirmation in the panel."),
    "spread": ("**Bid/ask spread and order book**: if the spread exceeds 0.5% or too few offers exist near the "
               "price, the bot postpones the purchase and retries every 5 minutes for 6 hours, so as not to buy at "
               "a bad price."),
    "fees": ("**Fees**: 0.1% per purchase and per sale on Binance Spot (less with BNB). The historical results "
             "shown include them, with 0.1% price slippage."),
    "trend": ("**Trend following**: buy what is already rising and keep it as long as the rise lasts. Many small "
              "losses, a few big gains that make the result."),
    "fng": ("**Fear & Greed index**: a barometer of the crypto market's mood, from 0 (extreme fear) to 100 "
            "(euphoria). The bot does not use it to decide; it is shown for information."),
    "dominance": ("**Bitcoin dominance**: bitcoin's share of the total value of cryptos. When it rises, other "
                  "cryptos often do less well."),
}


def gloss(key: str) -> Callable[[Dict[str, Any]], str]:
    return lambda ctx: GLOSSARY[key].format(**rules(ctx))


def a_hello(ctx: Dict[str, Any]) -> str:
    return ("Hello, delighted to see you again! What can I do for you? You can pick a question below or write "
            "your own.")


def a_who(ctx: Dict[str, Any]) -> str:
    return (f"My name is {NAME}, the assistant of your TrendGuard panel. " + SCOPE
            + " I can't change anything myself: I explain, you decide.")


def a_thanks(ctx: Dict[str, Any]) -> str:
    return "My pleasure! I remain at your disposal if you have another question."


def a_bye(ctx: Dict[str, Any]) -> str:
    return "Goodbye, and thank you for your visit! Have a lovely day, see you soon."


def a_objectives(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    mode = "real" if st.get("mode") == "live" else "paper (fictitious money, real prices)"
    r = rules(ctx)
    return (
        "**Goal**: capture the big crypto trends while limiting each loss.\n"
        f"- 21 cryptos quoted in USDT on Binance Spot, current mode: {mode}.\n"
        f"- Purchase when a crypto breaks its high of the last {r['breakout_n']} days and its underlying trend "
        "(90 days) is positive, only if bitcoin is above its "
        f"{r['regime_sma']}-day average (bull market).\n"
        "- Each purchase risks 1% of the capital; the bot can go up to 2% in steps if its analysis justifies it, "
        "and goes back to 1% at the first warning; 8 positions at most.\n"
        "- Sale when the price closes below a stop that rises with the price; a catastrophe stop placed on "
        "Binance against crashes; emergency stop at −40%.\n"
        "- Real history (fees included): about +37% a year from 2023 to 2026, with a worst drop of −34%. "
        "35 to 50% winning trades: the gains, larger than the losses, make the result. No guarantee for the "
        "future.")


def a_anticipation(ctx: Dict[str, Any]) -> str:
    """Likely sales and purchases at the next close, with current prices."""
    f = ctx.get("anticipation") or {}
    if not f.get("ready"):
        return ("The anticipation will be available after the bot's first decision (daily close at 00:00 UTC): I "
                "will then be able to tell you what it will probably do.")
    lines = [f"**Next decision in {hm(f.get('hours_left'))}** (00:00 UTC close). With the current prices:"]
    sells = [s for s in f.get("sells") or [] if s["prob"] >= 0.05][:4]
    for s in sells:
        lines.append(f"- 📉 {s['asset'].upper()}: sold if the close falls below {px(s['stop'])} "
                     f"({pc(s['dist_pct'])} from the price), probability {round(s['prob'] * 100)}%.")
    if f.get("sells") and not sells:
        lines.append("- 📉 No likely sale: all positions are far from their stop.")
    buys = [b for b in f.get("buys") or [] if b["prob"] >= 0.05][:4]
    for b in buys:
        blocked = ", but blocked" if b.get("blocked") else ""
        lines.append(f"- 📈 {b['asset'].upper()}: purchase if the close goes above {px(b['trigger'])} "
                     f"({pc(b['dist_pct'])} from the price), probability {round(b['prob'] * 100)}%{blocked}.")
    if not buys:
        lines.append("- 📈 No likely purchase before the close.")
    r = f.get("risk") or {}
    if r:
        lines.append(f"- Risk committed: {num(r.get('open_risk_pct'), 1)}% of the capital out of "
                     f"{num(r.get('budget_pct'), 0)}% allowed, {r.get('slots', 0)} free slot(s).")
    lines.append("These are indicative probabilities, not certainties: the bot decides alone at the close, with "
                 "its usual rules.")
    return "\n".join(lines)


def a_status(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    state = {"running": "running", "stopped": "stopped", "starting": "starting", "stopping": "stopping",
             "restarting": "restarting automatically"}.get(st.get("state"), st.get("state") or "unknown")
    lines = [f"The bot is **{state}** (mode {'real' if st.get('mode') == 'live' else 'paper'})."]
    if st.get("equity") is not None:
        ch = ""
        if st.get("start_equity"):
            ch = f", {pc((st['equity'] / st['start_equity'] - 1) * 100)} since the start"
        lines.append(f"- Capital: {num(st['equity'])} USDT{ch}.")
    lines.append(f"- Positions: {st.get('positions', 0)} / {st.get('max_positions', 8)}.")
    if st.get("drawdown_pct") is not None:
        lines.append("- Drop from the high: " + (f"{pc(st['drawdown_pct'])}." if st["drawdown_pct"] < 0
                                                  else "none, the capital is at its high."))
    if st.get("regime_bull") is not None:
        lines.append("- Market: " + ("bullish, purchases allowed." if st["regime_bull"] else "bearish, no purchase."))
    return "\n".join(lines)


def a_crypto_market(ctx: Dict[str, Any]) -> str:
    """The crypto market right now (bitcoin, market cap, dominance, Fear & Greed)."""
    news = ctx.get("news") or {}
    m = news.get("markets") or {}
    c, f = m.get("crypto"), m.get("fear_greed")
    lines = ["**Crypto market right now**:"]
    btc = (news.get("crypto_prices") or {}).get("btc") or {}
    if btc.get("price"):
        lines.append(f"- Bitcoin: ${num(btc['price'], 0)} ({pc(btc.get('change_pct'))} over 24 h).")
    if c:
        lines.append(f"- Total market cap: ${num(c['market_cap_usd'] / 1e9, 0)} billion "
                     f"({pc(c.get('market_cap_change_24h_pct'))} over 24 h); bitcoin dominance "
                     f"{num(c.get('btc_dominance_pct'), 1)}%.")
    if f:
        lines.append(f"- Fear & Greed: {f['value']}/100 (0 = extreme fear, 100 = euphoria).")
    st = ctx.get("status") or {}
    if st.get("regime_bull") is not None:
        sma = rules(ctx)["regime_sma"]
        lines.append("- For the bot: " + (f"BTC above its {sma}-day average, purchases allowed." if st["regime_bull"]
                                          else f"BTC below its {sma}-day average, no purchase."))
    heads = headlines(news, "crypto", 3)
    if heads:
        lines += ["Headlines:"] + heads
    if len(lines) == 1:
        lines.append("- Data loading: try again in a minute.")
    return "\n".join(lines)


def a_finance(ctx: Dict[str, Any]) -> str:
    news = ctx.get("news") or {}
    quotes = {q["id"]: q for q in (news.get("markets") or {}).get("quotes") or []}
    lines = ["**Financial markets (change of the day)**:"]
    for qid in ("sp500", "nasdaq", "cac40", "gold", "oil", "eurusd", "us10y"):
        q = quotes.get(qid)
        if q:
            unit = "%" if q.get("unit") == "%" else ""
            lines.append(f"- {q['name']}: {num(q['price'], 2 if q['price'] < 1000 else 0)}{unit} "
                         f"({pc(q.get('change_pct'), 2)}).")
    heads = headlines(news, "finance", 3)
    if heads:
        lines += ["Headlines:"] + heads
    if len(lines) == 1:
        lines.append("- Prices loading: try again in a minute.")
    lines.append("The bot only trades cryptos, but stocks, the dollar and interest rates often influence their "
                 "trend.")
    return "\n".join(lines)


def a_news(ctx: Dict[str, Any]) -> str:
    heads = headlines(ctx.get("news") or {}, None, 5)
    if not heads:
        return "The news is loading: try again in a minute."
    return "\n".join(["**Latest news** (third-party information, not verified):"] + heads)


def a_phone(ctx: Dict[str, Any]) -> str:
    urls = (ctx.get("status") or {}).get("lan_urls") or []
    now = (f"Wi-Fi access already active: open {urls[0]} on the phone." if urls else
           "Wi-Fi access is not active on this panel yet.")
    return (
        "**Connecting from a phone** (same Wi-Fi as the PC):\n"
        "- 1. On the PC, create your access: python trendguard_bot.py set-panel-password (masked input) and "
        "answer « o » to « accès depuis un téléphone ».\n"
        "- 2. The panel opens to the Wi-Fi at the next computer start; right away: VS Code task « Panneau — accès "
        "téléphone (port 8766) ».\n"
        "- 3. Windows asks to allow Python: tick « Private networks » only.\n"
        "- 4. On the phone, open the address shown in Settings ▸ On your phone (http://PC-address:port) and enter "
        "the password.\n"
        f"{now}\n"
        "Away from home (4G): never open a port on your router; install a free VPN such as Tailscale on the PC "
        "and the phone. Avoid public Wi-Fi.")


def a_phone_setup(ctx: Dict[str, Any]) -> str:
    return (
        "**Setting up the phone** (the panel becomes an app):\n"
        "- Android (Chrome): open the panel, menu ⋮ then « Add to home screen » or « Install app ».\n"
        "- iPhone (Safari): Share button then « Add to Home Screen ».\n"
        "- The TrendGuard icon then opens full screen, with the tab bar at the bottom.\n"
        "- Alerts on the phone: WhatsApp or e-mail with python trendguard_bot.py alerts configurer (on the PC), "
        "then python trendguard_bot.py alerts tester.\n"
        "There is no APK file: the panel would have to be published on the Internet, which would put the bot "
        "at risk.")


def a_interface(ctx: Dict[str, Any]) -> str:
    return (
        "**Using the panel**:\n"
        "- AUTO (top right) starts the automation; STOP stops it cleanly, with no restart. In real mode, a "
        "confirmation is asked.\n"
        "- Dashboard: capital, news ticker (it pauses under the mouse, a click opens the page), « What the bot "
        "thinks », positions and alerts.\n"
        "- News: crypto and financial markets, filterable articles.\n"
        "- Charts: a click opens the detail (candles, purchases, sales, stops, zoom).\n"
        "- Cryptos: the 21 cryptos, the reason for the bot's choice, « Watched » filter.\n"
        "- Positions, Watch, Log; Settings: start with the computer, alerts, theme, language, phone; Payment: "
        "the ways to pay in your country.\n"
        "- This Assistant button answers your questions; it can't change anything.")


def a_account(ctx: Dict[str, Any]) -> str:
    st = ctx.get("status") or {}
    active = ("A password is already set for this panel." if st.get("password") else
              "No password is set: the panel is only open on this PC.")
    return (
        "**Your access to the panel**: there is no public sign-up, because the panel controls a bot that "
        "manages money. Access is reserved for the owner:\n"
        "- On the PC itself, no password: only this PC can access it.\n"
        "- For a phone, create or change the password on the PC: python trendguard_bot.py set-panel-password "
        "(10 characters minimum, masked input).\n"
        "- Each device stays signed in for 30 days; « Log out » in Settings.\n"
        "- To sign out every device: change the password, then restart the computer.\n"
        f"{active}\n"
        "Never type this password in this window.")


def a_keys(ctx: Dict[str, Any]) -> str:
    return (
        "**Binance keys, safely**:\n"
        "- Create the key on Binance with reading and Spot trading, NEVER the withdrawal permission, and limit it "
        "to your connection's IP address if possible.\n"
        "- Save it on the PC with python trendguard_bot.py set-keys: masked input, checked with Binance, stored in "
        "the private .env file.\n"
        "- Never paste it in a conversation, an e-mail or this panel. If it happened: delete it on Binance and "
        "create a new one.\n"
        "- Turn on two-factor authentication (2FA) on your Binance account.")


def a_security(ctx: Dict[str, Any]) -> str:
    sec = ctx.get("security") or {}
    checks = sec.get("checks") or []
    lines = ["**Your security**" + (f": {sec.get('ok', 0)} protections out of {sec.get('total', 0)} are green."
                                    if checks else ".")]
    lines += ["My advice:",
              "- Never type a key, a password or a code in a message, even here.",
              "- Binance key without the withdrawal permission, limited to your IP address; two-factor "
              "authentication (2FA) on the account.",
              "- Away from home, use a VPN (Tailscale), never an open port on the router.",
              "- Beware of messages promising gains or asking for your codes: Binance will never ask for them.",
              "- The details of each check are in Settings ▸ Security report."]
    return "\n".join(lines)


def a_alerts(ctx: Dict[str, Any]) -> str:
    return ("**Alerts**: Telegram, e-mail and WhatsApp. On the PC: python trendguard_bot.py alerts configurer "
            "(masked input), then python trendguard_bot.py alerts tester, or the « Test » button in Settings. By "
            "default, only critical alerts go by e-mail and WhatsApp (ALERT_LEVEL=all to receive everything).")


def a_autonomy(ctx: Dict[str, Any]) -> str:
    au = (ctx.get("status") or {}).get("autonomy") or {}
    auto = "on" if au.get("autostart") else "off"
    return ("**Autonomy**: AUTO starts a supervisor that restarts the bot if it crashes or freezes. Starting with "
            f"the computer is {auto} (Settings ▸ Autonomy). The PC does not go to sleep by itself while the bot "
            "runs. STOP is respected: no restart, even after a reboot, until the next AUTO.")


def a_report(ctx: Dict[str, Any]) -> str:
    r = ctx.get("report") or {}
    if not r.get("ready"):
        return ("**Daily report**: every day at 00:30 UTC, the bot makes a deep analysis (security, health, "
                "strategy, skills, code) and sends it to you by e-mail and WhatsApp. The first one is not ready "
                "yet: Settings ▸ Daily report ▸ Generate now.")
    s = r.get("score") or {}
    return (f"**Daily report of {r.get('day')}**: {s.get('ok', 0)} compliant checks out of {s.get('total', 0)}. "
            "Full report (in French): Settings ▸ Daily report ▸ Full report.")


def a_advice(ctx: Dict[str, Any]) -> str:
    return ("I give neither personalised investment advice nor price forecasts: nobody knows tomorrow's price. "
            "The bot applies fixed rules, tested over 8 years, and risks 1% per purchase. Its decision of the day "
            "and the cryptos it watches are in « What the bot thinks ». Only invest what you can afford to lose.")


def a_sell(ctx: Dict[str, Any]) -> str:
    lines = ["**The bot also sells, automatically**: each purchase is sold when the day's close falls below its "
             "trailing stop. This stop rises with the price and never goes down: the gain is locked in as it "
             "goes, and the sale happens when the trend runs out of steam. A catastrophe stop placed on Binance "
             "protects against a crash between two closes."]
    for p in ((ctx.get("positions") or {}).get("positions") or [])[:8]:
        if not p.get("entry") or not p.get("stop"):
            continue
        locked = p["stop"] / p["entry"] - 1
        lines.append(f"- {p['asset'].upper()}: sale if the close falls below {px(p['stop'])}; "
                     f"{'gain' if locked >= 0 else 'loss'} locked at {pc(locked * 100)}.")
    lines.append("Selling earlier, at a fixed gain, did worse in the past: +0.45 to +0.54 R per trade against "
                 "+0.70 R by letting the gain run. Sales appear as a red ▼ on the charts.")
    return "\n".join(lines)


def a_selection(ctx: Dict[str, Any]) -> str:
    sel = (ctx.get("status") or {}).get("selection") or {}
    n, total = len(sel.get("active") or []), sel.get("universe") or 21
    mode = "auto selection" if sel.get("mode") == "auto" else "manual selection"
    return ("**Choosing the bot's cryptos** (Cryptos page):\n"
            "- Auto selection: the bot may buy all 21 cryptos, all ticked. It is the default, recommended setting.\n"
            "- Manual selection: at first, the 10 most profitable cryptos over 2 years are ticked; tick or untick "
            "the others (as long as none is ticked, the bot buys nothing).\n"
            "- A held crypto that leaves the selection stays managed until it is sold.\n"
            f"Currently: {mode}, {n} buyable crypto(s) out of {total}.")


def a_watch(ctx: Dict[str, Any]) -> str:
    return ("**Watch**: every day, the bot reads Binance's official announcements (a crypto that Binance delists "
            "is no longer bought) and, if AIs are configured, their opinion on the news, as advice only. The "
            "details are in the Watch tab.")


def a_risk(ctx: Dict[str, Any]) -> str:
    text = GLOSSARY["risk"]
    f = ctx.get("anticipation") or {}
    r = f.get("risk") or {}
    if f.get("ready") and r:
        text += (f"\nRight now: {r.get('positions', 0)} position(s), risk committed {num(r.get('open_risk_pct'), 1)}% "
                 f"out of {num(r.get('budget_pct'), 0)}% allowed, {r.get('slots', 0)} free slot(s) for new "
                 "purchases.")
    return text


def a_bourses(ctx: Dict[str, Any]) -> str:
    b = ctx.get("bourses") or {}
    rows = b.get("rows") or []
    if not rows:
        return "The world's stock exchanges are described on the « World stock exchanges » page of the panel."
    opened = [r["name"].split(" (")[0] for r in rows if (r.get("status") or {}).get("open")]
    lines = [f"**World stock exchanges**: {len(rows)} exchanges followed, {len(opened)} in session right now"
             + (f" ({', '.join(opened[:5])})" if opened else "") + "."]
    lines.append("To invest from your country: an approved intermediary for your country's exchange; elsewhere, a "
                 "regulated international broker that accepts residents of your country (to be checked).")
    lines.append("The details of each exchange (hours, indices, regulator, access from your country) are on the "
                 "« World stock exchanges » page. The bot reads these exchanges for its knowledge; it places no "
                 "order there.")
    return "\n".join(lines)


def a_courtiers(ctx: Dict[str, Any]) -> str:
    c = ctx.get("courtiers") or {}
    acc = c.get("accounts") or {}
    lines = ["**Brokers and demo accounts** (never a real account, no order sent):"]
    for key, label in (("mt5", "MetaTrader 5 (currencies, gold, indices)"),
                       ("alpaca", "Alpaca (US stocks, paper account)")):
        s = acc.get(key) or {}
        lines.append(f"- {label}: {STATES.get(str(s.get('state')), 'not connected')}.")
    lines.append("Connect a demo account: python trendguard_bot.py courtiers set-key mt5 (or alpaca, bybit…), "
                 "masked input, account checked before being kept.")
    return "\n".join(lines)


# Account states (trendguard/courtiers.py, STATES, same keys), in English.
STATES = {"absent": "not connected", "attente": "not read yet", "demo": "demo connected", "refuse": "REFUSED",
          "erreur": "error", "ferme": "terminal closed", "indisponible": "unavailable"}


def a_paiements(ctx: Dict[str, Any]) -> str:
    country = str(ctx.get("country") or "CI")
    v = paiements.methods(country)
    lines = [f"**Paying and getting paid in {v['name_en']}** (information, to be checked with each service):"]
    for k in v["kinds"]:
        names = [m["name_en"] for m in v["methods"] if m["kind"] == k["code"]]
        if names:
            lines.append(f"- {k['en']}: {', '.join(names[:6])}.")
    lines.append("For the bot: USDT can be bought on Binance (P2P with your local methods, or by card); never "
                 "release USDT before you see the money arrive.")
    lines.append(v["safety"][0]["en"])
    lines.append("The details are on the « Payment » page of the menu.")
    return "\n".join(lines)


EN: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "hello": a_hello, "who": a_who, "thanks": a_thanks, "bye": a_bye, "objectives": a_objectives,
    "anticipation": a_anticipation, "status": a_status, "crypto_market": a_crypto_market, "finance": a_finance,
    "news": a_news, "phone": a_phone, "phone_setup": a_phone_setup, "interface": a_interface,
    "account": a_account, "keys": a_keys, "security": a_security, "alerts": a_alerts, "autonomy": a_autonomy,
    "report": a_report, "advice": a_advice, "sell": a_sell, "selection": a_selection, "watch": a_watch,
    "risk": a_risk, "bourses": a_bourses, "courtiers": a_courtiers, "paiements": a_paiements,
    **{k: gloss(k) for k in ("stop", "breakout", "regime", "r", "drawdown", "paper", "spread", "fees", "trend",
                             "fng", "dominance")},
}
# English keywords of each topic (normalised: lower case, no accents nor
# punctuation) ; the French ones still count, a person may mix both.
KEYS: Dict[str, Tuple[str, ...]] = {
    "hello": ("hi", "hello", "hey", "good morning", "good afternoon", "good evening", "help"),
    "who": ("who are you", "your name", "what can you do", "introduce yourself", "what are you"),
    "thanks": ("thanks", "thank you", "great", "perfect", "awesome", "excellent"),
    "bye": ("goodbye", "bye", "see you", "good night", "have a nice day"),
    "objectives": ("goal", "goals", "objective", "strategy", "what does the bot do", "how does the bot work",
                   "how does it work", "purpose", "returns", "expected performance"),
    "anticipation": ("tonight", "tomorrow", "next close", "what will the bot do", "will the bot", "will sell",
                     "will buy", "next sale", "next purchase", "probability", "probable", "next decision",
                     "worst case"),
    "status": ("status", "state", "bot state", "state of the bot", "balance", "capital", "how much", "profit",
               "loss", "result", "running", "positions", "is the bot running", "portfolio value"),
    "crypto_market": ("crypto market", "~crypto", "~cryptos", "~bitcoin", "~btc", "~ethereum", "how is the market",
                      "~market", "market trend", "altcoins"),
    "finance": ("stock market", "stocks", "shares", "s p 500", "nasdaq", "dow jones", "gold", "oil", "~dollar",
                "interest rates", "fed", "inflation", "wall street"),
    "bourses": ("stock exchange", "stock exchanges", "world exchanges", "regulator", "invest from",
                "invest in stocks", "buy shares", "nyse", "london", "johannesburg", "lagos", "nairobi", "tokyo"),
    "courtiers": ("broker", "brokers", "demo account", "metatrader", "mt5", "alpaca", "paper account", "testnet",
                  "testnets"),
    "news": ("news", "headlines", "latest news"),
    "phone": ("phone", "smartphone", "mobile phone", "remote", "remotely", "wifi", "wi fi", "connect", "vpn",
              "android", "iphone", "4g"),
    "phone_setup": ("set up my phone", "set up the phone", "install", "home screen", "app", "apk", "pwa"),
    "interface": ("how to use", "use the panel", "~panel", "button", "tab", "menu", "dashboard", "user guide",
                  "how does the panel work"),
    "account": ("account", "sign up", "register", "password", "login", "log in", "log out", "logout", "access",
                "create my access"),
    "keys": ("api key", "api keys", "binance key", "2fa", "two factor", "withdrawal permission"),
    "security": ("security", "secure", "safe", "am i safe", "protect", "protection", "hack", "hacker", "hacked",
                 "scam", "scams", "phishing"),
    "alerts": ("alert", "alerts", "whatsapp", "email", "e mail", "notification", "notifications", "telegram",
               "sms"),
    "autonomy": ("autonomous", "autonomy", "restart", "reboot", "computer", "start with the computer", "crash",
                 "supervisor"),
    "report": ("report", "daily report", "security report", "backup", "backups"),
    "advice": ("should i", "advice", "prediction", "forecast", "will it go up", "will it go down", "best crypto",
               "what to buy", "invest"),
    "sell": ("sell", "selling", "sold", "sale", "sales", "take profit", "profits", "gains", "when does it sell"),
    "selection": ("selection", "select", "choose cryptos", "most profitable", "tick", "untick"),
    "watch": ("watch", "~ai", "announcements", "delisting", "delisted", "blocked"),
    "stop": ("stop", "stop loss", "trailing stop", "catastrophe stop"),
    "breakout": ("breakout", "new high"),
    "regime": ("regime", "moving average", "bull market", "bear market", "bullish", "bearish"),
    "risk": ("risk", "risks", "position size", "1 percent", "how much does it bet"),
    "r": ("r multiple", "what does r mean", "in r"),
    "drawdown": ("drawdown", "emergency stop", "kill switch"),
    "paper": ("paper", "paper trading", "fake money", "real money", "live", "real mode"),
    "spread": ("spread", "order book"),
    "fees": ("fees", "fee", "commission"),
    "trend": ("trend following", "trend"),
    "fng": ("fear", "greed", "fear and greed"),
    "dominance": ("dominance",),
    "paiements": ("payment", "payments", "pay", "how to pay", "how do i pay", "payment methods", "mobile money",
                  "bank card", "prepaid card", "debit card", "credit card", "bank", "banking", "bank transfer",
                  "deposit", "withdraw money", "top up", "orange money", "mtn momo", "m pesa", "wave", "paypal",
                  "visa", "mastercard", "money transfer", "western union"),
}


def why(src: str, cycle_label: str, sources: List[str]) -> List[str]:
    """« Why? » of an answer, in English (panel/interface.py)."""
    out = ["answered by " + ("an AI, checked (invented amounts refused)" if src not in ("local", "")
                             else "Rachelle on this PC, without AI (written rules)")]
    out.append(f"bot state: {cycle_label}")
    out += [f"source: {s}" for s in sources]
    out.append("she does not act: no answer places an order or changes a setting")
    return out


def country_of(ctx: Dict[str, Any], device_country: str) -> str:
    """Le pays dont parler : celui de l'appareil s'il est connu, sinon celui
    du profil du bot."""
    iso = str(device_country or "").strip().upper()
    if iso in profil.names():
        return iso
    return str(((ctx.get("bourses") or {}).get("profile") or {}).get("country") or profil.DEFAULT_COUNTRY)


# Keywords in other major languages (Spanish, Portuguese, German, Italian,
# Russian, Chinese, Arabic, Japanese, Korean, Hindi, Turkish), so that a
# question asked without device translation still finds its topic. Latin
# script: normalised (lower case, no accents); other scripts: searched as is.
WORLD_KEYS: Dict[str, Tuple[str, ...]] = {
    "hello": ("hola", "buenos dias", "buenas tardes", "ola", "bom dia", "boa tarde", "hallo", "guten tag",
              "guten morgen", "ciao", "buongiorno", "merhaba", "привет", "здравствуйте", "добрый день", "你好",
              "您好", "مرحبا", "السلام عليكم", "こんにちは", "안녕하세요", "नमस्ते"),
    "who": ("quien eres", "como te llamas", "quem e voce", "como se chama", "wer bist du", "wie heisst du",
            "chi sei", "кто ты", "как тебя зовут", "你是谁", "你叫什么", "من أنت", "ما اسمك"),
    "thanks": ("gracias", "obrigado", "obrigada", "danke", "grazie", "tesekkurler", "tesekkur ederim", "спасибо",
               "谢谢", "شكرا", "ありがとう", "감사합니다", "धन्यवाद"),
    "bye": ("adios", "hasta luego", "tchau", "ate logo", "tschuss", "auf wiedersehen", "arrivederci",
            "до свидания", "пока", "再见", "مع السلامة", "وداعا"),
    "status": ("estado del bot", "saldo", "ganancias", "estado do bot", "kontostand", "guthaben", "stato del bot",
               "статус", "баланс", "капитал", "状态", "余额", "资金", "حالة", "رصيد", "رأس المال"),
    "objectives": ("objetivo", "estrategia", "como funciona", "ziel", "strategie", "wie funktioniert",
                   "obiettivo", "strategia", "come funziona", "цель", "стратегия", "как работает", "目标", "策略",
                   "如何运作", "怎么运作", "هدف", "استراتيجية", "كيف يعمل"),
    "paiements": ("pago", "pagos", "pagar", "tarjeta", "banco", "transferencia", "pagamento", "pagamentos",
                  "cartao", "zahlung", "zahlungen", "bezahlen", "karte", "uberweisung", "ueberweisung",
                  "pagare", "carta", "banca", "bonifico", "odeme", "banka", "оплата", "платеж", "платить",
                  "карта", "банк", "перевод", "支付", "付款", "银行卡", "银行", "转账", "دفع", "الدفع", "بطاقة",
                  "بنك", "تحويل", "支払い", "銀行", "결제", "은행", "भुगतान"),
    "security": ("seguridad", "seguranca", "sicherheit", "sicurezza", "guvenlik", "безопасность", "安全", "أمان",
                 "الأمان", "حماية"),
    "crypto_market": ("mercado cripto", "criptomonedas", "criptomoedas", "kryptowahrung", "kryptomarkt",
                      "criptovalute", "крипто", "биткоин", "加密货币", "比特币", "العملات المشفرة", "بيتكوين"),
    "interface": ("como usar", "panel de control", "painel", "bedienung", "wie benutze", "come usare", "pannello",
                  "как пользоваться", "панель", "怎么用", "如何使用", "面板", "كيف أستخدم", "لوحة"),
    "phone": ("telefono", "movil", "celular", "telefone", "handy", "telefon", "cellulare", "телефон", "手机",
              "هاتف", "الهاتف"),
    "advice": ("debo comprar", "consejo", "prediccion", "devo comprar", "conselho", "previsao", "soll ich",
               "prognose", "devo comprare", "consiglio", "previsione", "стоит ли", "совет", "прогноз", "建议",
               "应该买", "预测", "نصيحة", "هل يجب", "توقع"),
    "sell": ("vender", "venta", "venda", "verkaufen", "verkauf", "vendere", "vendita", "продать", "продажа",
             "卖出", "出售", "بيع"),
    "risk": ("riesgo", "risco", "risiko", "rischio", "риск", "风险", "مخاطر", "المخاطرة"),
    "news": ("noticias", "nachrichten", "notizie", "haberler", "новости", "新闻", "أخبار"),
    "bourses": ("bolsa", "bolsas", "bolsa de valores", "borse", "boerse", "aktien", "borsa", "биржа", "交易所",
                "股市", "بورصة", "البورصة"),
}
