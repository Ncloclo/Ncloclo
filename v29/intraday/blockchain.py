"""Wallet EVM (optionnel).

Partie de l'ancien bot V29.6 (v29/intraday/), rangé à part du moteur
d'exécution que TrendGuard utilise.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Optional, Tuple

from ..config import Config
from ..infra import Notifier
from ..models import BotContext
from ..store import Store
from ..utils import _parse_iso, _utcnow, _utcnow_iso, redact_address, redact_url, scrub_secrets

# Dépendance optionnelle : wallet EVM (web3).
try:
    from eth_account import Account
    from web3 import Web3
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
        except ImportError:
            ExtraDataToPOAMiddleware = None
    WEB3_AVAILABLE = True
except ImportError:
    Web3 = None
    Account = None
    WEB3_AVAILABLE = False
    ExtraDataToPOAMiddleware = None

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
     "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"},
                                     {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "success", "type": "bool"}],
     "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]



class BlockchainError(Exception):
    pass


def _to_wei_exact(amount: float, decimals: int = 18) -> int:
    """Conversion exacte (Decimal) — jamais de float * 10**18."""
    q = Decimal(str(amount)) * (Decimal(10) ** int(decimals))
    return int(q.to_integral_value(rounding=ROUND_DOWN))


class BlockchainPolicy:
    def __init__(self, cfg: Config, self_address: str,
                 logger: logging.Logger):
        self.cfg = cfg
        self.self_address = self_address.lower()
        self.logger = logger

    def guard_tx(self, to: str, value_eth: float) -> str:
        if not Web3.is_address(to):
            raise BlockchainError(f"Adresse invalide: {to}")
        to_cs = Web3.to_checksum_address(to)
        if to_cs.lower() == self.self_address:
            raise BlockchainError("Destination = émetteur (refus).")
        if self.cfg.blockchain_whitelist_enabled:
            wl = {a.lower() for a in self.cfg.blockchain_whitelist}
            if not wl:
                raise BlockchainError("Whitelist activée mais vide.")
            if to_cs.lower() not in wl:
                raise BlockchainError(f"Destination {to_cs} non autorisée.")
        if not isinstance(value_eth, (int, float)) or not math.isfinite(value_eth):
            raise BlockchainError("value_eth non fini.")
        if value_eth < 0:
            raise BlockchainError("value_eth négatif.")
        if value_eth > self.cfg.blockchain_max_tx_value_eth:
            raise BlockchainError(
                f"value_eth={value_eth} > "
                f"max={self.cfg.blockchain_max_tx_value_eth}")
        return to_cs


class BlockchainAdapter:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 store: Optional[Store] = None,
                 notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.logger = logger
        self.store = store
        self.notifier = notifier or Notifier("", "")
        self.enabled = bool(cfg.blockchain_enabled)
        self.w3: Optional[Any] = None
        self.account: Optional[Any] = None
        self.address: Optional[str] = None
        self.policy: Optional[BlockchainPolicy] = None
        self._nonce_lock = threading.Lock()
        # Sémantique UNIQUE : prochain nonce libre (jamais « dernier utilisé »).
        self._next_nonce: Optional[int] = None
        self._token_contract: Optional[Any] = None
        self._eip1559_supported: Optional[bool] = None
        self._secrets = [cfg.blockchain_rpc_url]
        if not self.enabled:
            return
        if not WEB3_AVAILABLE:
            raise BlockchainError("web3 non installé.")
        pk = os.environ.get("BLOCKCHAIN_PRIVATE_KEY", "").strip()
        if not pk:
            raise BlockchainError("BLOCKCHAIN_PRIVATE_KEY absent.")
        if not pk.startswith("0x"):
            pk = "0x" + pk
        self._secrets.append(pk)
        try:
            self.account = Account.from_key(pk)
            self.address = self.account.address
        except Exception:
            raise BlockchainError("Clé privée invalide.")
        try:
            self.w3 = Web3(Web3.HTTPProvider(
                cfg.blockchain_rpc_url, request_kwargs={"timeout": 20}))
            if cfg.blockchain_chain.lower() in ("bsc", "polygon"):
                self._inject_poa()
            if not self.w3.is_connected():
                raise BlockchainError(
                    f"RPC injoignable: {redact_url(cfg.blockchain_rpc_url)}")
            remote_chain_id = self.w3.eth.chain_id
            if remote_chain_id != cfg.blockchain_chain_id:
                raise BlockchainError(
                    f"chain_id mismatch: config={cfg.blockchain_chain_id} "
                    f"remote={remote_chain_id}")
            self._detect_eip1559()
            bal_eth = self.balance_native()
            self.logger.info(
                f"[CHAIN] OK chain={cfg.blockchain_chain} "
                f"id={cfg.blockchain_chain_id} "
                f"address={redact_address(self.address)} "
                f"balance={bal_eth:.6f} dry_run={cfg.blockchain_dry_run}")
            if cfg.blockchain_track_token:
                if not Web3.is_address(cfg.blockchain_track_token):
                    raise BlockchainError(
                        f"Adresse ERC-20 invalide: "
                        f"{cfg.blockchain_track_token}")
                addr_cs = Web3.to_checksum_address(cfg.blockchain_track_token)
                code = self.w3.eth.get_code(addr_cs)
                if not code or len(code) < 10:
                    raise BlockchainError(
                        f"Adresse {addr_cs} n'est pas un contrat.")
                self._token_contract = self.w3.eth.contract(
                    address=addr_cs, abi=ERC20_ABI)
            self.policy = BlockchainPolicy(cfg, self.address, logger)
        except BlockchainError:
            raise
        except Exception as e:
            raise BlockchainError(f"Init Web3 KO: {self._scrub(e)}")

    def _scrub(self, e: Any) -> str:
        return scrub_secrets(str(e), self._secrets)

    def _inject_poa(self) -> None:
        if ExtraDataToPOAMiddleware is None:
            raise BlockchainError("Chaîne POA mais middleware absent.")
        try:
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            return
        except AttributeError:
            pass
        except Exception as e:
            self.logger.warning(f"[POA] inject KO: {self._scrub(e)}")
        try:
            self.w3.middleware.add(ExtraDataToPOAMiddleware)
        except Exception as e:
            raise BlockchainError(f"Middleware POA non injectable: {self._scrub(e)}")

    def _detect_eip1559(self) -> None:
        try:
            latest = self.w3.eth.get_block("latest")
            self._eip1559_supported = bool(latest.get("baseFeePerGas"))
        except Exception as e:
            self.logger.warning(f"[CHAIN] EIP-1559 detect KO: {self._scrub(e)}")
            self._eip1559_supported = bool(self._eip1559_supported)

    def balance_native(self) -> float:
        if not self.enabled:
            return 0.0
        try:
            return float(self.w3.from_wei(
                self.w3.eth.get_balance(self.address), "ether"))
        except Exception as e:
            raise BlockchainError(f"balance_native KO: {self._scrub(e)}")

    def balance_token(self, token_addr: Optional[str] = None) -> float:
        if not self.enabled:
            return 0.0
        try:
            contract = self._token_contract
            if contract is None and token_addr:
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_addr),
                    abi=ERC20_ABI)
            if contract is None:
                return 0.0
            decimals = contract.functions.decimals().call()
            raw = contract.functions.balanceOf(self.address).call()
            return float(Decimal(raw) / (Decimal(10) ** int(decimals)))
        except Exception as e:
            raise BlockchainError(f"balance_token KO: {self._scrub(e)}")

    def _compute_fees(self) -> Tuple[int, int, bool]:
        cap = int(self.w3.to_wei(self.cfg.blockchain_max_fee_gwei, "gwei"))
        if not self._eip1559_supported:
            gp = int(self.w3.eth.gas_price)
            if gp > cap:
                raise BlockchainError(
                    f"gas_price={gp} > cap={cap} — tx reportée")
            return gp, gp, False
        latest = self.w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas") or 0)
        cap_priority = int(self.w3.to_wei(
            self.cfg.blockchain_max_priority_fee_gwei, "gwei"))
        try:
            s = self.w3.eth.max_priority_fee
            s = int(s() if callable(s) else s)
        except Exception:
            s = cap_priority // 4
        priority = max(1, min(s, cap_priority))
        if base_fee + priority > cap:
            raise BlockchainError(
                f"base_fee={base_fee} + priority={priority} > cap={cap} — "
                f"tx reportée")
        max_fee = min(base_fee * 2 + priority, cap)
        return max_fee, priority, True

    # ---------- Nonce ----------

    def _acquire_nonce(self) -> int:
        """Prochain nonce libre = max(nonce pending du nœud, compteur local).
        Aucune estimation « à l'aveugle » si le nœud est injoignable."""
        with self._nonce_lock:
            try:
                chain = int(self.w3.eth.get_transaction_count(
                    self.address, "pending"))
            except Exception as e:
                raise BlockchainError(f"Nonce indisponible: {self._scrub(e)}")
            n = chain if self._next_nonce is None else max(chain,
                                                           self._next_nonce)
            self._next_nonce = n + 1
            return n

    def _resync_nonce(self) -> None:
        with self._nonce_lock:
            try:
                self._next_nonce = int(self.w3.eth.get_transaction_count(
                    self.address, "pending"))
            except Exception:
                self._next_nonce = None

    # ---------- Envoi ----------

    def _sign_and_send(self, tx: Dict[str, Any]) -> str:
        signed = self.w3.eth.account.sign_transaction(
            tx, private_key=self.account.key)
        raw = (getattr(signed, "raw_transaction", None)
               or signed.rawTransaction)
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        h = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        return h if h.startswith("0x") else "0x" + h

    def _build_tx(self, nonce: int, to_cs: str, value_wei: int, gas: int,
                  max_fee: int, priority: int, eip1559: bool,
                  data: Optional[str] = None) -> Dict[str, Any]:
        if eip1559:
            tx = {"type": 2, "chainId": self.cfg.blockchain_chain_id,
                  "nonce": nonce, "from": self.address, "to": to_cs,
                  "value": value_wei, "gas": gas,
                  "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority}
        else:
            tx = {"chainId": self.cfg.blockchain_chain_id, "nonce": nonce,
                  "from": self.address, "to": to_cs, "value": value_wei,
                  "gas": gas, "gasPrice": max_fee}
        if data:
            tx["data"] = data
        return tx

    @staticmethod
    def _encode_transfer(contract: Any, to_cs: str, raw_amount: int) -> str:
        """Encodage ERC-20 compatible web3 v6/v7/v8."""
        fn = contract.functions.transfer(to_cs, raw_amount)
        enc = getattr(fn, "_encode_transaction_data", None)
        if callable(enc):
            return enc()
        enc_abi = getattr(contract, "encode_abi", None)
        if callable(enc_abi):
            try:
                return enc_abi("transfer", args=[to_cs, raw_amount])
            except TypeError:
                return enc_abi(fn_name="transfer", args=[to_cs, raw_amount])
        return contract.encodeABI(fn_name="transfer", args=[to_cs, raw_amount])

    def _track_pending(self, ctx: Optional[BotContext], tx_hash: str,
                       nonce: int, max_fee: int, to: str, value_wei: int,
                       data: Optional[str], gas: int, kind: str,
                       priority_wei: int = 0) -> None:
        if ctx is None:
            return
        b = ctx.blockchain
        b.last_pending_tx_hash = tx_hash
        b.pending_tx_hashes = [tx_hash]
        b.last_pending_tx_since = _utcnow_iso()
        b.last_pending_tx_nonce = nonce
        b.last_pending_tx_fee_wei = str(max_fee)
        b.last_pending_tx_priority_wei = str(priority_wei)
        b.last_pending_tx_to = to
        b.last_pending_tx_value_wei = str(value_wei)
        b.last_pending_tx_data = data
        b.last_pending_tx_gas = gas
        b.last_pending_tx_kind = kind
        b.rbf_attempts = 0
        b.rbf_exhausted_notified = False

    def _clear_pending(self, ctx: Optional[BotContext],
                       resync_nonce: bool = False) -> None:
        if ctx is None:
            return
        b = ctx.blockchain
        b.last_pending_tx_hash = None
        b.pending_tx_hashes = []
        b.last_pending_tx_since = None
        b.last_pending_tx_nonce = None
        b.last_pending_tx_fee_wei = None
        b.last_pending_tx_priority_wei = None
        b.last_pending_tx_to = None
        b.last_pending_tx_value_wei = None
        b.last_pending_tx_data = None
        b.last_pending_tx_gas = None
        b.last_pending_tx_kind = None
        b.rbf_attempts = 0
        b.rbf_exhausted_notified = False
        if resync_nonce:
            self._resync_nonce()

    def _check_no_pending(self, ctx: Optional[BotContext]) -> None:
        if ctx is None:
            return
        if ctx.blockchain.last_pending_tx_hash:
            raise BlockchainError(
                f"Pending en vol: "
                f"{ctx.blockchain.last_pending_tx_hash[:16]}… "
                f"(attendre résolution/RBF)")

    def send_native(self, to: str, amount_eth: float,
                    ctx: Optional[BotContext] = None) -> Dict[str, Any]:
        if self.policy is None:
            raise BlockchainError("Policy absente")
        to_cs = self.policy.guard_tx(to, amount_eth)
        self._check_no_pending(ctx)
        if ctx is None and not self.cfg.blockchain_dry_run:
            raise BlockchainError("send_native live sans contexte : refus "
                                  "(la tx ne serait pas suivie)")
        value_wei = _to_wei_exact(amount_eth)
        native_bal = self.balance_native()
        max_fee, priority, eip1559 = self._compute_fees()
        gas_estimate = 21000
        gas_cost_eth = float(self.w3.from_wei(gas_estimate * max_fee, "ether"))
        required = (amount_eth + gas_cost_eth
                    + self.cfg.blockchain_min_gas_reserve_eth)
        if native_bal < required:
            if self.cfg.blockchain_dry_run:
                self.logger.warning(
                    f"[DRY-RUN] solde insuffisant ({native_bal:.6f} < "
                    f"{required:.6f})")
            else:
                raise BlockchainError(
                    f"Solde insuffisant: {native_bal:.6f} < {required:.6f}")
        if self.cfg.blockchain_dry_run:
            self.logger.info(
                f"[DRY-RUN] send_native → {redact_address(to_cs)} "
                f"{amount_eth} (non diffusé)")
            return {"tx_hash": "DRY_RUN", "dry_run": True, "pending": False,
                    "value_eth": amount_eth, "to": to_cs}
        nonce = self._acquire_nonce()
        tx = self._build_tx(nonce, to_cs, value_wei, gas_estimate,
                            max_fee, priority, eip1559)
        try:
            tx_hash = self._sign_and_send(tx)
        except Exception as e:
            self._resync_nonce()
            raise BlockchainError(f"send_native KO: {self._scrub(e)}")
        self._track_pending(
            ctx, tx_hash, nonce, max_fee, to_cs, value_wei, None,
            gas_estimate, "native",
            priority_wei=(priority if eip1559 else max_fee))
        self.logger.info(
            f"[CHAIN] send_native: {amount_eth} → {redact_address(to_cs)} "
            f"hash={tx_hash[:16]}… nonce={nonce}")
        return {"tx_hash": tx_hash, "dry_run": False, "pending": True,
                "value_eth": amount_eth, "to": to_cs}

    def send_token(self, token_addr: str, to: str, amount_token: float,
                   ctx: Optional[BotContext] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise BlockchainError("blockchain disabled")
        if not Web3.is_address(token_addr):
            raise BlockchainError(f"Token invalide: {token_addr}")
        if self.policy is None:
            raise BlockchainError("Policy absente")
        to_cs = self.policy.guard_tx(to, 0.0)
        self._check_no_pending(ctx)
        if ctx is None and not self.cfg.blockchain_dry_run:
            raise BlockchainError("send_token live sans contexte : refus")
        token_cs = Web3.to_checksum_address(token_addr)
        contract = self.w3.eth.contract(address=token_cs, abi=ERC20_ABI)
        decimals = int(contract.functions.decimals().call())
        raw_amount = _to_wei_exact(amount_token, decimals)
        if raw_amount <= 0:
            raise BlockchainError("Montant token nul après conversion.")
        bal = contract.functions.balanceOf(self.address).call()
        if bal < raw_amount:
            raise BlockchainError(
                f"Balance token insuffisante: "
                f"{Decimal(bal) / (Decimal(10) ** decimals)} < {amount_token}")
        native_bal = self.balance_native()
        try:
            gas_estimate = contract.functions.transfer(
                to_cs, raw_amount).estimate_gas({"from": self.address})
        except Exception as e:
            raise BlockchainError(f"Estimation gaz ERC-20 KO: {self._scrub(e)}")
        gas_estimate = int(gas_estimate * 1.3)
        max_fee, priority, eip1559 = self._compute_fees()
        gas_cost_eth = float(self.w3.from_wei(gas_estimate * max_fee, "ether"))
        required = gas_cost_eth + self.cfg.blockchain_min_gas_reserve_eth
        if native_bal < required:
            if self.cfg.blockchain_dry_run:
                self.logger.warning(
                    f"[DRY-RUN] gaz insuffisant ({native_bal:.6f} < "
                    f"{required:.6f})")
            else:
                raise BlockchainError(
                    f"Gaz insuffisant: {native_bal:.6f} < {required:.6f}")
        if self.cfg.blockchain_dry_run:
            self.logger.info(
                f"[DRY-RUN] send_token {amount_token} → "
                f"{redact_address(to_cs)} (non diffusé)")
            return {"tx_hash": "DRY_RUN", "dry_run": True, "pending": False,
                    "token_amount": amount_token,
                    "token_addr": token_cs, "to": to_cs}
        data = self._encode_transfer(contract, to_cs, raw_amount)
        nonce = self._acquire_nonce()
        tx = self._build_tx(nonce, token_cs, 0, gas_estimate,
                            max_fee, priority, eip1559, data=data)
        try:
            tx_hash = self._sign_and_send(tx)
        except Exception as e:
            self._resync_nonce()
            raise BlockchainError(f"send_token KO: {self._scrub(e)}")
        self._track_pending(
            ctx, tx_hash, nonce, max_fee, token_cs, 0, data,
            gas_estimate, "token",
            priority_wei=(priority if eip1559 else max_fee))
        self.logger.info(
            f"[CHAIN] send_token: {amount_token} → {redact_address(to_cs)} "
            f"hash={tx_hash[:16]}… nonce={nonce}")
        return {"tx_hash": tx_hash, "dry_run": False, "pending": True,
                "token_amount": amount_token,
                "token_addr": token_cs, "to": to_cs}

    # ---------- Suivi ----------

    def poll_pending_tx(self, ctx: BotContext) -> Optional[Dict[str, Any]]:
        if not self.enabled or self.cfg.blockchain_dry_run:
            return None
        b = ctx.blockchain
        if not b.last_pending_tx_hash:
            return None
        hashes = list(dict.fromkeys(
            list(b.pending_tx_hashes or []) + [b.last_pending_tx_hash]))
        receipt = None
        mined_hash = None
        for h in hashes:
            try:
                r = self.w3.eth.get_transaction_receipt(h)
            except Exception:
                r = None
            if r:
                receipt, mined_hash = r, h
                break
        kind = b.last_pending_tx_kind or "native"
        if receipt is None:
            nonce = b.last_pending_tx_nonce
            if nonce is not None:
                try:
                    latest = int(self.w3.eth.get_transaction_count(
                        self.address, "latest"))
                    if latest > int(nonce):
                        self.logger.error(
                            f"[CHAIN] nonce {nonce} consommé par une tx "
                            f"inconnue (wallet partagé ?) → suivi arrêté")
                        self.notifier(
                            f"⚠️ Nonce {nonce} consommé hors bot", critical=True)
                        self._clear_pending(ctx, resync_nonce=True)
                        return {"tx_hash": None, "status": None,
                                "kind": kind, "replaced_externally": True}
                except Exception:
                    pass
            since = _parse_iso(b.last_pending_tx_since)
            if since and (_utcnow() - since).total_seconds() \
                    > self.cfg.blockchain_tx_timeout_sec:
                self.check_stuck_tx(ctx)
            return None
        try:
            head = int(self.w3.eth.block_number)
            confs = head - int(receipt.get("blockNumber") or head) + 1
        except Exception:
            confs = 0
        if confs < self.cfg.blockchain_confirmations:
            return None
        status = int(receipt.get("status", 0))
        gas_used = int(receipt.get("gasUsed", 0))
        eff_price = int(receipt.get("effectiveGasPrice")
                        or int(b.last_pending_tx_fee_wei or "0"))
        value_wei = int(b.last_pending_tx_value_wei or "0")
        gas_eth = float(self.w3.from_wei(gas_used * eff_price, "ether"))
        if status != 1:
            self.logger.error(
                f"[CHAIN] REVERT ({kind}): hash={mined_hash} gas={gas_used}")
            self.notifier(f"❌ Tx revert: {mined_hash[:10]}…", critical=True)
        else:
            self.logger.info(
                f"[CHAIN] OK ({kind}): hash={mined_hash} gas={gas_used} "
                f"confs={confs}")
        if self.store:
            self.store.log_blockchain_tx({
                "chain": self.cfg.blockchain_chain,
                "chain_id": self.cfg.blockchain_chain_id,
                "tx_hash": mined_hash, "from_addr": self.address,
                "to_addr": b.last_pending_tx_to,
                "value_eth": float(self.w3.from_wei(value_wei, "ether")),
                "token_addr": None, "token_amount": None,
                "gas_used": gas_used, "gas_price_wei": str(eff_price),
                "status": status, "dry_run": 0, "pending": 0,
                "rbf": 1 if b.rbf_attempts > 0 else 0},
                self.cfg.run_mode)
        b.tx_count = int(b.tx_count or 0) + 1
        b.total_gas_spent_eth = float(b.total_gas_spent_eth or 0.0) + gas_eth
        self._clear_pending(ctx)
        return {"tx_hash": mined_hash, "status": status,
                "gas_used": gas_used, "kind": kind, "confirmations": confs}

    def check_stuck_tx(self, ctx: BotContext) -> bool:
        """Remplace (RBF) une tx bloquée. Au-delà de max_rbf_attempts on
        cesse de surenchérir mais on CONTINUE le suivi : une tx diffusée
        n'est jamais « oubliée » (elle peut encore être minée)."""
        if not self.enabled or self.cfg.blockchain_dry_run:
            return False
        b = ctx.blockchain
        tx_hash = b.last_pending_tx_hash
        if not tx_hash:
            return False
        attempts = int(b.rbf_attempts or 0)
        if attempts >= self.cfg.blockchain_max_rbf_attempts:
            if not b.rbf_exhausted_notified:
                self.logger.error(
                    f"[RBF] {tx_hash[:16]}… — {attempts} remplacements, "
                    f"arrêt des surenchères (suivi maintenu)")
                self.notifier(f"❌ Tx {tx_hash[:10]}… bloquée : action "
                              f"manuelle requise", critical=True)
                b.rbf_exhausted_notified = True
            return False
        nonce = b.last_pending_tx_nonce
        if nonce is None:
            self.logger.error("[RBF] nonce manquant → suivi arrêté")
            self._clear_pending(ctx, resync_nonce=True)
            return False
        old_fee = int(b.last_pending_tx_fee_wei or "0")
        old_priority = int(b.last_pending_tx_priority_wei or "0")
        boost = 1.0 + self.cfg.blockchain_replace_fee_boost_pct
        try:
            _mf, net_priority, eip1559 = self._compute_fees()
            cap_fee = int(self.w3.to_wei(self.cfg.blockchain_max_fee_gwei, "gwei"))
            cap_priority = int(self.w3.to_wei(
                self.cfg.blockchain_max_priority_fee_gwei, "gwei"))
            if eip1559:
                new_priority = max(net_priority, int(old_priority * boost) + 1)
                new_fee = max(_mf, int(old_fee * boost) + 1,
                              new_priority + 1)
                if new_priority > cap_priority or new_fee > cap_fee:
                    self.logger.error(
                        f"[RBF] plafond atteint (prio={new_priority} "
                        f"fee={new_fee}) → attente")
                    b.rbf_attempts = attempts + 1
                    return False
            else:
                new_fee = max(int(self.w3.eth.gas_price),
                              int(old_fee * boost) + 1)
                if new_fee > cap_fee:
                    self.logger.error(f"[RBF] plafond legacy {new_fee} > {cap_fee}")
                    b.rbf_attempts = attempts + 1
                    return False
                new_priority = new_fee
            tx = self._build_tx(
                int(nonce), b.last_pending_tx_to,
                int(b.last_pending_tx_value_wei or "0"),
                int(b.last_pending_tx_gas or 21000), new_fee, new_priority,
                eip1559, data=b.last_pending_tx_data)
            new_hash = self._sign_and_send(tx)
        except BlockchainError as e:
            self.logger.error(f"[RBF] KO: {e}")
            b.rbf_attempts = attempts + 1
            return False
        except Exception as e:
            self.logger.error(f"[RBF] KO: {self._scrub(e)}")
            b.rbf_attempts = attempts + 1
            return False
        b.pending_tx_hashes = list(dict.fromkeys(
            list(b.pending_tx_hashes or [tx_hash]) + [new_hash]))
        b.last_pending_tx_hash = new_hash
        b.last_pending_tx_since = _utcnow_iso()
        b.last_pending_tx_fee_wei = str(new_fee)
        b.last_pending_tx_priority_wei = str(new_priority)
        b.rbf_attempts = attempts + 1
        self.logger.warning(
            f"[RBF] #{attempts + 1}: {tx_hash[:10]}… → {new_hash[:10]}… "
            f"(fee {old_fee}→{new_fee}, prio {old_priority}→{new_priority})")
        self.notifier(f"🔄 RBF #{attempts + 1} {tx_hash[:10]}…")
        return True

    def refresh_state(self, ctx: BotContext) -> None:
        if not self.enabled:
            return
        try:
            ctx.blockchain.last_balance_eth = self.balance_native()
            if self._token_contract is not None:
                ctx.blockchain.last_balance_token = self.balance_token()
            chain_nonce = int(self.w3.eth.get_transaction_count(
                self.address, "pending"))
            ctx.blockchain.last_nonce = chain_nonce
            with self._nonce_lock:
                if self._next_nonce is None:
                    self._next_nonce = chain_nonce
                elif (self._next_nonce > chain_nonce
                      and ctx.blockchain.last_pending_tx_hash is None):
                    # Une tx locale a disparu du mempool : on se recale.
                    self._next_nonce = chain_nonce
            ctx.blockchain.last_block_seen = int(self.w3.eth.block_number)
            self._detect_eip1559()
            ctx.blockchain.eip1559_supported = self._eip1559_supported
        except Exception as e:
            self.logger.warning(f"[CHAIN] refresh_state KO: {self._scrub(e)}")

    def maybe_sweep(self, ctx: BotContext) -> bool:
        if not self.enabled or not self.cfg.blockchain_sweep_enabled:
            return False
        if ctx.blockchain.last_pending_tx_hash:
            return False
        now = time.time()
        if now - float(ctx.blockchain.last_sweep_ts or 0) < 600:
            return False
        if (now - float(ctx.blockchain.last_sweep_fail_ts or 0)
                < self.cfg.blockchain_sweep_fail_cooldown_sec):
            return False
        try:
            bal = self.balance_native()
            trigger = self.cfg.blockchain_sweep_trigger_eth
            keep = self.cfg.blockchain_sweep_keep_eth
            if bal <= trigger:
                return False
            max_fee, _, _ = self._compute_fees()
            gas_cost = float(self.w3.from_wei(21000 * max_fee, "ether"))
            amount = bal - keep - gas_cost * 1.5
            if amount <= 0:
                return False
            amount = min(amount, self.cfg.blockchain_max_tx_value_eth)
            res = self.send_native(self.cfg.blockchain_sweep_target, amount, ctx)
            ctx.blockchain.last_sweep_ts = now
            if res.get("dry_run"):
                self.logger.info(f"[SWEEP][DRY-RUN] {amount:.6f} (non diffusé)")
                return False
            ctx.blockchain.sweep_count = int(ctx.blockchain.sweep_count or 0) + 1
            self.notifier(
                f"💸 Sweep {amount:.6f} → "
                f"{redact_address(self.cfg.blockchain_sweep_target)}")
            return True
        except BlockchainError as e:
            self.logger.info(f"[SWEEP] reporté: {e}")
            ctx.blockchain.last_sweep_fail_ts = now
            return False
        except Exception as e:
            self.logger.warning(f"[SWEEP] KO: {self._scrub(e)}")
            ctx.blockchain.last_sweep_fail_ts = now
            return False
