"""Plus de cryptos (research/univers_crypto.py) : seules les vraies cryptos
liquides de Binance hors des 21 du bot sont candidates."""

import json

from research import univers_crypto as uc


def test_candidates_are_liquid_cryptos_outside_the_bot_universe():
    tickers = [{"symbol": s, "quoteVolume": str(v)} for s, v in (
        ("SOLUSDT", 2e8), ("BTCUSDT", 9e9),            # BTC : déjà dans les 21
        ("NVDABUSDT", 9e7), ("PAXGUSDT", 1e7),          # action et or tokenisés
        ("RLUSDUSDT", 5e7), ("FDUSDUSDT", 5e8),         # stablecoins
        ("BTCUPUSDT", 6e6), ("NEARUSDT", 8e7),          # jeton à levier ; NEAR
        ("PEPEUSDT", 4e6), ("NEARBTC", 1e9))]           # trop peu échangé ; autre devise de cotation
    info = {"data": [{"assetCode": "SOL", "assetName": "Solana", "tags": ["Layer1_Layer2"]},
                     {"assetCode": "NVDAB", "assetName": "NVIDIA (bStocks)", "tags": ["bStocks"]},
                     {"assetCode": "RLUSD", "assetName": "Ripple USD", "tags": ["Stablecoin"]},
                     {"assetCode": "NEAR", "assetName": "NEAR Protocol", "tags": []}]}

    def fetch(url):
        return json.dumps(info if url == uc.ASSETS_INFO else tickers).encode()
    got = uc.candidates(fetch)
    assert [c["base"] for c in got] == ["sol", "near"] and got[0]["name"] == "Solana"
