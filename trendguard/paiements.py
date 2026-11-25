"""
Paiements et banque près de chez vous (docs/PAIEMENTS.md) : les moyens de
payer et d'être payé dans votre pays et votre zone (paiement mobile, cartes
de débit et prépayées, passerelles de paiement en ligne, virements, envoi
d'argent), et comment passer de votre monnaie aux USDT du bot puis revenir.

Le pays vient de votre profil (TG_PAYS) ou, dans le panneau, de l'appareil
qui le regarde (fuseau horaire et langue, position si vous l'autorisez :
elle n'est jamais gardée et ne part vers aucun service extérieur).
Information seulement : le bot ne reçoit, n'envoie ni ne garde aucun
paiement, et ne demande jamais un numéro de carte, un code ou un mot de
passe. Noms d'opérateurs et de services connus ; conditions, frais et
disponibilité changent : à vérifier auprès de chacun.

    python trendguard_bot.py paiements [pays]
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import profil

# Familles de moyens de paiement : (code, nom français, nom anglais).
KINDS: Tuple[Tuple[str, str, str], ...] = (
    ("mobile", "Paiement mobile", "Mobile payments"),
    ("carte", "Cartes bancaires (débit et prépayées)", "Bank cards (debit and prepaid)"),
    ("en_ligne", "Paiement en ligne et passerelles", "Online payments and gateways"),
    ("virement", "Banque et virements", "Banking and transfers"),
    ("transfert", "Envoi d'argent", "Money transfers"),
    ("crypto", "Vers les USDT du bot et retour", "To the bot's USDT and back"))
KIND_CODES = tuple(k[0] for k in KINDS)
SCOPES = ("pays", "zone", "monde")
Care = Tuple[str, str]
NONE: Care = ("", "")
VERIFY: Care = ("disponibilité et frais à vérifier dans votre pays", "availability and fees to be checked in your country")
P2P: Care = ("ne libérez jamais les USDT avant de voir l'argent sur votre compte, et restez dans la messagerie de "
             "Binance : les arnaques passent par l'extérieur", "never release USDT before you see the money in your "
             "account, and stay inside Binance's chat: scams happen outside it")


class Method:
    """Un moyen de paiement : famille, nom, ce qu'il permet (français et
    anglais), une prudence éventuelle."""

    __slots__ = ("kind", "name", "name_en", "fr", "en", "care")

    def __init__(self, kind: str, name: str, fr: str, en: str, care: Care = NONE, name_en: str = ""):
        self.kind, self.name, self.name_en, self.fr, self.en, self.care = kind, name, name_en or name, fr, en, care


def _mm(name: str, op: str = "", care: Care = NONE) -> Method:
    """Un porte-monnaie mobile (mobile money) d'un opérateur."""
    de = "d'" if op[:1].lower() in "aeiouéèêàâîôœ" else "de "
    fr = "porte-monnaie mobile" + (f" {de}{op}" if op else "") + " : envoyer, payer, retirer chez les agents"
    en = "mobile wallet" + (f" by {op}" if op else "") + ": send, pay, cash out at agents"
    return Method("mobile", name, fr, en, care)


def _gate(name: str, fr_where: str, en_where: str, care: Care = NONE) -> Method:
    """Une passerelle de paiement en ligne (sites et applications marchandes)."""
    return Method("en_ligne", name, f"passerelle de paiement en ligne {fr_where} : cartes et paiement mobile",
                  f"online payment gateway {en_where}: cards and mobile money", care)


# Partout (ou presque).
GLOBAL: Tuple[Method, ...] = (
    Method("carte", "Visa et Mastercard (débit ou prépayées)", "cartes liées à un compte, ou prépayées et "
           "rechargeables (banques, opérateurs, fintechs) : achats en ligne, retraits",
           "debit cards linked to an account, or prepaid reloadable cards (banks, operators, fintechs): online "
           "purchases, withdrawals", name_en="Visa and Mastercard (debit or prepaid)"),
    Method("en_ligne", "PayPal", "paiement en ligne ; services ouverts (envoyer, recevoir, retirer) selon le pays",
           "online payments; available services (send, receive, withdraw) depend on the country", VERIFY),
    Method("en_ligne", "Apple Pay et Google Pay", "payer avec son téléphone grâce à une carte enregistrée",
           "pay with your phone using a saved card", VERIFY, name_en="Apple Pay and Google Pay"),
    Method("virement", "Virement international (SWIFT)", "depuis votre banque, en dollars ou en euros ; frais fixes "
           "et de change", "from your bank, in dollars or euros; fixed and exchange fees",
           name_en="International transfer (SWIFT)"),
    Method("transfert", "Western Union et MoneyGram", "envoi et retrait d'argent en agence ou en ligne, partout dans "
           "le monde", "send and collect money at agents or online, worldwide", VERIFY,
           name_en="Western Union and MoneyGram"),
    Method("transfert", "Envoi vers le paiement mobile (Wise, WorldRemit, Remitly, Sendwave, Taptap Send…)",
           "depuis l'étranger vers un compte bancaire ou un porte-monnaie mobile, souvent moins cher qu'en agence",
           "from abroad to a bank account or a mobile wallet, often cheaper than agents", VERIFY,
           name_en="Transfers to mobile money (Wise, WorldRemit, Remitly, Sendwave, Taptap Send…)"),
    Method("crypto", "Binance P2P", "acheter et vendre des USDT entre particuliers, payés par vos moyens locaux "
           "(paiement mobile, virement) ; Binance garde les USDT pendant l'échange", "buy and sell USDT between "
           "individuals, paid with your local methods (mobile money, bank transfer); Binance holds the USDT during "
           "the trade", P2P),
    Method("crypto", "Carte bancaire chez Binance", "acheter des USDT directement par carte Visa ou Mastercard, avec "
           "des frais", "buy USDT directly with a Visa or Mastercard card, with fees", VERIFY,
           name_en="Bank card on Binance"),
)
# Par zone monétaire (profil.zone_of).
ZONES: Dict[str, Tuple[Method, ...]] = {
    "UEMOA": (
        Method("carte", "Cartes GIM-UEMOA", "réseau interbancaire de l'UEMOA : cartes de débit et prépayées des banques "
               "des huit pays", "the WAEMU interbank network: debit and prepaid cards from the banks of the eight "
               "countries", name_en="GIM-UEMOA cards"),
        Method("virement", "Virement dans l'UEMOA", "en FCFA, sans change, entre les banques des huit pays",
               "in CFA francs, no exchange, between the banks of the eight countries",
               name_en="Transfers within WAEMU")),
    "CEMAC": (
        Method("carte", "Cartes GIMAC", "réseau interbancaire de la CEMAC : cartes des banques des six pays",
               "the CEMAC interbank network: cards from the banks of the six countries", name_en="GIMAC cards"),
        Method("virement", "Virement dans la CEMAC", "en FCFA, sans change, entre les banques des six pays",
               "in CFA francs, no exchange, between the banks of the six countries",
               name_en="Transfers within CEMAC")),
    "zone euro": (
        Method("virement", "Virement SEPA (instantané ou classique)", "en euros dans l'espace SEPA, souvent gratuit, "
               "souvent en quelques secondes", "in euros across the SEPA area, often free, often within seconds",
               name_en="SEPA transfer (instant or standard)"),),
}
# Par pays : les moyens les plus connus (les autres pays ont ceux de leur
# zone et ceux de partout).
COUNTRIES: Dict[str, Tuple[Method, ...]] = {
    # Afrique de l'Ouest (UEMOA)
    "CI": (_mm("Orange Money", "Orange"), _mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Moov Money", "Moov Africa"),
           _mm("Wave"), _gate("CinetPay", "ivoirienne", "from Côte d'Ivoire"),
           Method("carte", "Djamo", "application et carte Visa prépayée", "app and prepaid Visa card", VERIFY)),
    "SN": (_mm("Orange Money", "Orange"), _mm("Wave"), _gate("PayDunya", "sénégalaise", "from Senegal")),
    "BF": (_mm("Orange Money", "Orange"), _mm("Moov Money", "Moov Africa"), _mm("Wave", care=VERIFY)),
    "ML": (_mm("Orange Money", "Orange"), _mm("Moov Money", "Moov Africa"), _mm("Wave", care=VERIFY)),
    "BJ": (_mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Moov Money", "Moov Africa"),
           _gate("FedaPay", "béninoise", "from Benin", VERIFY)),
    "TG": (_mm("Flooz", "Moov Africa"), _mm("T-Money", "Togocom", VERIFY)),
    "NE": (_mm("Airtel Money", "Airtel"), _mm("Moov Money", "Moov Africa")),
    "GW": (_mm("Orange Money", "Orange"), _mm("MTN Mobile Money (MoMo)", "MTN")),
    # Afrique de l'Ouest (hors UEMOA)
    "GN": (_mm("Orange Money", "Orange"), _mm("MTN Mobile Money (MoMo)", "MTN")),
    "GM": (_mm("Wave"), _mm("Afrimoney", "Africell", VERIFY)),
    "SL": (_mm("Orange Money", "Orange"), _mm("Afrimoney", "Africell")),
    "LR": (_mm("Orange Money", "Orange"), _mm("MTN Mobile Money (MoMo)", "Lonestar Cell MTN")),
    "NG": (Method("virement", "Virements instantanés NIP", "d'une banque nigériane à l'autre en quelques secondes "
                  "(réseau NIBSS)", "between Nigerian banks within seconds (NIBSS network)",
                  name_en="NIP instant transfers"),
           _gate("Paystack", "nigériane", "from Nigeria"), _gate("Flutterwave", "nigériane", "from Nigeria"),
           _mm("OPay"), _mm("PalmPay"), _mm("Moniepoint"),
           Method("carte", "Cartes Verve", "le réseau de cartes nigérian", "the Nigerian card network",
                  name_en="Verve cards")),
    "GH": (_mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Telecel Cash", "Telecel"), _mm("AT Money", "AT"),
           _gate("Hubtel", "ghanéenne", "from Ghana")),
    # Afrique centrale
    "CM": (_mm("Orange Money", "Orange"), _mm("MTN Mobile Money (MoMo)", "MTN")),
    "GA": (_mm("Airtel Money", "Airtel"), _mm("Moov Money", "Moov Africa")),
    "CG": (_mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Airtel Money", "Airtel")),
    "TD": (_mm("Airtel Money", "Airtel"), _mm("Moov Money", "Moov Africa")),
    "CF": (_mm("Orange Money", "Orange"),),
    "CD": (_mm("M-Pesa", "Vodacom"), _mm("Orange Money", "Orange"), _mm("Airtel Money", "Airtel")),
    # Afrique de l'Est et australe
    "KE": (_mm("M-Pesa", "Safaricom"), _mm("Airtel Money", "Airtel"),
           Method("virement", "PesaLink", "virement instantané entre banques kényanes", "instant transfer between "
                  "Kenyan banks")),
    "TZ": (_mm("M-Pesa", "Vodacom"), _mm("Airtel Money", "Airtel")),
    "UG": (_mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Airtel Money", "Airtel"), _mm("Wave", care=VERIFY)),
    "RW": (_mm("MTN Mobile Money (MoMo)", "MTN"), _mm("Airtel Money", "Airtel")),
    "ET": (_mm("telebirr", "Ethio Telecom"), _mm("M-Pesa", "Safaricom")),
    "MG": (_mm("MVola", "Telma"), _mm("Orange Money", "Orange"), _mm("Airtel Money", "Airtel")),
    "ZA": (Method("virement", "EFT et PayShap", "virement entre banques sud-africaines, PayShap en quelques "
                  "secondes", "transfer between South African banks, PayShap within seconds",
                  name_en="EFT and PayShap"),
           Method("mobile", "SnapScan et Zapper", "paiement par code QR chez les marchands", "QR code payments at "
                  "merchants", name_en="SnapScan and Zapper")),
    # Afrique du Nord
    "EG": (_mm("Vodafone Cash", "Vodafone"),
           Method("virement", "InstaPay", "virement instantané entre banques égyptiennes", "instant transfer between "
                  "Egyptian banks"),
           Method("en_ligne", "Fawry", "paiement de factures et d'achats en ligne ou en point de vente", "bill and "
                  "online payments, online or at outlets"),
           Method("carte", "Cartes Meeza", "le réseau de cartes égyptien", "the Egyptian card network",
                  name_en="Meeza cards")),
    "MA": (_mm("Orange Money", "Orange"),
           Method("en_ligne", "CMI", "le centre monétique interbancaire marocain : paiement par carte en ligne",
                  "Morocco's interbank card centre: online card payments")),
    "DZ": (Method("carte", "Cartes CIB et Edahabia", "carte interbancaire des banques algériennes ; Edahabia "
                  "d'Algérie Poste", "the Algerian banks' interbank card; Edahabia from Algérie Poste",
                  name_en="CIB and Edahabia cards"),),
    "TN": (Method("carte", "e-Dinar", "carte prépayée de La Poste tunisienne", "prepaid card from the Tunisian Post",
                  care=VERIFY),),
    # Amériques
    "US": (Method("virement", "ACH", "virement entre banques américaines", "transfer between US banks"),
           Method("mobile", "Zelle", "envoi d'argent entre comptes bancaires", "send money between bank accounts"),
           Method("mobile", "Venmo et Cash App", "envoi d'argent par application", "send money with an app",
                  name_en="Venmo and Cash App")),
    "CA": (Method("virement", "Virement Interac", "envoi d'argent par courriel ou téléphone entre banques "
                  "canadiennes", "send money by email or phone between Canadian banks", name_en="Interac e-Transfer"),),
    "BR": (Method("virement", "Pix", "paiement instantané de la Banque centrale du Brésil", "instant payments run by "
                  "the Central Bank of Brazil"),
           Method("en_ligne", "Boleto", "bordereau payable en ligne ou en agence", "payment slip, online or at "
                  "branches")),
    "MX": (Method("virement", "SPEI", "virement instantané entre banques mexicaines", "instant transfer between "
                  "Mexican banks"),
           Method("en_ligne", "OXXO Pay", "payer en espèces, en magasin, une commande passée en ligne", "pay cash, in "
                  "store, for an online order")),
    "AR": (_mm("Mercado Pago"),),
    "CO": (_mm("Nequi"), Method("en_ligne", "PSE", "paiement en ligne depuis votre banque", "online payment from "
                                "your bank")),
    "PE": (Method("mobile", "Yape et Plin", "envoi d'argent par téléphone entre banques", "send money by phone "
                  "between banks", name_en="Yape and Plin"),),
    # Europe
    "GB": (Method("virement", "Faster Payments", "virement instantané entre banques britanniques", "instant transfer "
                  "between UK banks"),),
    "FR": (Method("carte", "Cartes CB", "le réseau de cartes bancaires français", "the French bank card network",
                  name_en="CB cards"),),
    "BE": (Method("carte", "Bancontact", "cartes et paiement mobile belges", "Belgian cards and mobile payments"),),
    "NL": (Method("en_ligne", "iDEAL", "paiement en ligne depuis votre banque", "online payment from your bank"),),
    "CH": (Method("mobile", "TWINT", "paiement mobile suisse", "Swiss mobile payments"),),
    "PT": (Method("mobile", "MB WAY", "paiement mobile portugais", "Portuguese mobile payments"),),
    "ES": (Method("mobile", "Bizum", "envoi d'argent entre banques espagnoles", "send money between Spanish banks"),),
    # Asie et Océanie
    "IN": (Method("mobile", "UPI (PhonePe, Google Pay, Paytm…)", "paiement instantané depuis votre compte bancaire",
                  "instant payments from your bank account"),
           Method("carte", "Cartes RuPay", "le réseau de cartes indien", "the Indian card network",
                  name_en="RuPay cards")),
    "CN": (Method("mobile", "Alipay et WeChat Pay", "paiement mobile", "mobile payments", name_en="Alipay and WeChat Pay"),
           Method("carte", "UnionPay", "le réseau de cartes chinois", "the Chinese card network")),
    "JP": (Method("mobile", "PayPay", "paiement mobile par code QR", "QR code mobile payments"),),
    "KR": (Method("mobile", "KakaoPay et Naver Pay", "paiement mobile et en ligne", "mobile and online payments",
                  name_en="KakaoPay and Naver Pay"),),
    "SG": (Method("virement", "PayNow", "virement instantané par numéro de téléphone", "instant transfer by phone "
                  "number"),),
    "MY": (Method("virement", "DuitNow", "virement instantané par numéro de téléphone", "instant transfer by phone "
                  "number"), _mm("Touch 'n Go eWallet")),
    "TH": (Method("virement", "PromptPay", "virement instantané par numéro de téléphone ou code QR", "instant "
                  "transfer by phone number or QR code"),),
    "PK": (_mm("JazzCash", "Jazz"), _mm("Easypaisa")),
    "BD": (_mm("bKash"),),
    "PH": (_mm("GCash"), _mm("Maya")),
    "ID": (_mm("GoPay"), _mm("OVO"), _mm("DANA")),
    "AU": (Method("virement", "PayID (Osko)", "virement instantané par numéro de téléphone ou courriel",
                  "instant transfer by phone number or email"),),
    # Moyen-Orient
    "SA": (Method("carte", "Cartes mada", "le réseau de cartes saoudien", "the Saudi card network",
                  name_en="mada cards"),),
}
SAFETY: Tuple[Care, ...] = (
    ("Le bot ne reçoit, n'envoie ni ne garde aucun paiement : il ne vous demandera jamais un numéro de carte, un "
     "code, un mot de passe ou un code reçu par SMS.", "The bot never receives, sends or keeps any payment: it will "
     "never ask for a card number, a PIN, a password or a code received by SMS."),
    ("Ne donnez jamais votre code secret ni un code reçu par SMS, même à quelqu'un qui se dit de votre banque ou de "
     "votre opérateur.", "Never give your PIN or a code received by SMS, even to someone claiming to be from your "
     "bank or operator."),
    ("Vérifiez le nom du bénéficiaire avant de confirmer un envoi : un paiement mobile envoyé ne se rattrape pas.",
     "Check the recipient's name before confirming a transfer: mobile money sent cannot be recalled."),
    ("Préférez les services agréés dans votre pays et leurs applications officielles ; comparez les frais (change, "
     "retrait, envoi) avant d'envoyer.", "Prefer services licensed in your country and their official apps; compare "
     "the fees (exchange, withdrawal, transfer) before sending."),
)


def _row(m: Method, scope: str) -> Dict[str, Any]:
    return {"kind": m.kind, "name": m.name, "name_en": m.name_en, "fr": m.fr, "en": m.en,
            "care_fr": m.care[0], "care_en": m.care[1], "scope": scope}


def methods(country: str) -> Dict[str, Any]:
    """Les moyens de paiement d'un pays : les siens, ceux de sa zone
    monétaire, puis ceux de partout, rangés par famille."""
    country = (country or "").strip().upper()
    zone = profil.zone_of(country)
    own = COUNTRIES.get(country, ())
    rows = ([_row(m, "pays") for m in own] + [_row(m, "zone") for m in ZONES.get(zone, ())]
            + [_row(m, "monde") for m in GLOBAL])
    rows.sort(key=lambda r: KIND_CODES.index(r["kind"]))            # tri stable : le pays d'abord
    return {"country": country, "name": profil.names().get(country, country),
            "name_en": profil.english_names().get(country, country), "zone": zone,
            "currency": profil.currency_of(country), "known": bool(own), "methods": rows,
            "kinds": [{"code": c, "fr": f, "en": e} for c, f, e in KINDS],
            "safety": [{"fr": f, "en": e} for f, e in SAFETY],
            "languages": profil.languages_of(country)}


def view(country: str = "", gcfg: Any = None) -> Dict[str, Any]:
    """Ce que montre la page « Paiement » : les moyens du pays demandé (celui
    du profil du bot sinon), et les pays dont le bot connaît les moyens."""
    prof = profil.of_config(gcfg) if gcfg is not None else profil.profile()
    country = str(country or "").strip().upper()
    if country and country not in profil.names():
        raise ValueError("pays inconnu (code ISO à deux lettres, ex. CI, SN, NG, FR)")
    out = methods(country or prof["country"])
    out["profile"] = prof
    out["covered"] = sorted(COUNTRIES)
    return out


def render(v: Dict[str, Any]) -> str:
    """Texte de la commande : les moyens d'un pays, famille par famille."""
    lines = [f"Moyens de paiement : {v['name']} ({v['country']})"
             + (f", zone {v['zone']}" if v.get("zone") else "") + f", monnaie {v['currency']}"]
    if not v.get("known"):
        lines.append("  (moyens propres à ce pays pas encore recensés : ceux de partout ci-dessous)")
    for k in v["kinds"]:
        rows = [m for m in v["methods"] if m["kind"] == k["code"]]
        if rows:
            lines.append(f"\n{k['fr']}")
            for m in rows:
                care = f" — {m['care_fr']}" if m["care_fr"] else ""
                lines.append(f"  - {m['name']} ({m['scope']}) : {m['fr']}{care}")
    lines.append("\nÀ savoir :")
    lines += [f"  - {s['fr']}" for s in v["safety"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    country = args[0].upper() if args else ""
    if country and country not in profil.names():
        print(f"Pays inconnu : {country} (code ISO à deux lettres, ex. CI, SN, NG, FR)")
        return 2
    print(render(view(country)))
    return 0


def countries_covered() -> List[str]:
    return sorted(COUNTRIES)
