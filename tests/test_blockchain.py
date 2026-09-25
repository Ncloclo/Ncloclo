"""Wallet EVM : nonce, confirmations, suivi RBF, encodage — avec un faux w3."""

import hashlib
import json
import logging
import threading
from datetime import timedelta

import pytest

import v29
from conftest import make_cfg

pytestmark = pytest.mark.skipif(not v29.WEB3_AVAILABLE, reason="web3 absent")

ADDR = "0x" + "11" * 20
DEST = "0x" + "22" * 20


class FakeEth:
    def __init__(self):
        self.pending = 7
        self.latest = 7
        self.block_number = 100
        self.receipts = {}
        self.sent = []
        self.gas_price = 10 ** 9
        self.max_priority_fee = 2 * 10 ** 9
        self.fail_count = False
        self.account = self

    def get_transaction_count(self, addr, tag="latest"):
        if self.fail_count:
            raise ConnectionError("rpc down https://rpc.example/v3/SECRETKEY9999")
        return self.pending if tag == "pending" else self.latest

    def get_block(self, tag):
        return {"baseFeePerGas": 10 ** 9}

    def get_balance(self, addr):
        return 10 ** 18

    def get_transaction_receipt(self, h):
        if h in self.receipts:
            return self.receipts[h]
        raise ValueError("TransactionNotFound")

    def sign_transaction(self, tx, private_key=None):
        class S:
            pass
        s = S()
        s.raw_transaction = json.dumps(tx, sort_keys=True).encode()
        return s

    def send_raw_transaction(self, raw):
        self.sent.append(json.loads(raw))
        return bytes.fromhex(hashlib.sha256(raw).hexdigest())


class FakeW3:
    def __init__(self):
        self.eth = FakeEth()

    @staticmethod
    def from_wei(v, unit):
        return float(v) / (10 ** 18 if unit == "ether" else 10 ** 9)

    @staticmethod
    def to_wei(v, unit):
        return int(float(v) * (10 ** 18 if unit == "ether" else 10 ** 9))


def make_adapter(**cfg_kw):
    cfg = make_cfg("paper", blockchain_enabled=True, blockchain_rpc_url=
                   "https://rpc.example/v3/SECRETKEY9999",
                   blockchain_dry_run=False, blockchain_whitelist=(DEST,),
                   **cfg_kw)
    ba = v29.BlockchainAdapter.__new__(v29.BlockchainAdapter)
    ba.cfg = cfg
    ba.logger = logging.getLogger("test.chain")
    ba.store = None
    ba.notifier = v29.Notifier("", "")
    ba.enabled = True
    ba.w3 = FakeW3()

    class Acct:
        key = b"\x01" * 32
    ba.account = Acct()
    ba.address = ADDR
    ba._nonce_lock = threading.Lock()
    ba._next_nonce = None
    ba._token_contract = None
    ba._eip1559_supported = True
    ba._secrets = [cfg.blockchain_rpc_url]
    ba.policy = v29.BlockchainPolicy(cfg, ADDR, ba.logger)
    return ba


def test_nonce_no_gap_after_refresh():
    """V29.5 (FIX #39) : refresh_state stockait le prochain nonce libre puis
    _acquire_nonce ajoutait +1 → trou de nonce, tx bloquée à vie."""
    ba = make_adapter()
    ctx = v29.BotContext()
    ba.refresh_state(ctx)
    assert ba._acquire_nonce() == 7
    assert ba._acquire_nonce() == 8          # nœud en retard : pas de réutilisation


def test_nonce_resync_does_not_skip():
    ba = make_adapter()
    ba._next_nonce = 12
    ba._resync_nonce()
    assert ba._acquire_nonce() == 7


def test_nonce_unavailable_fails_closed_and_scrubbed():
    ba = make_adapter()
    ba.w3.eth.fail_count = True
    with pytest.raises(v29.BlockchainError) as ei:
        ba._acquire_nonce()
    assert "SECRETKEY9999" not in str(ei.value)


def test_send_waits_for_confirmations_and_uses_effective_gas():
    ba = make_adapter(blockchain_confirmations=2)
    ctx = v29.BotContext()
    res = ba.send_native(DEST, 0.001, ctx)
    h = res["tx_hash"]
    assert ctx.blockchain.last_pending_tx_hash == h
    assert ba.w3.eth.sent[0]["nonce"] == 7
    ba.w3.eth.receipts[h] = {"status": 1, "gasUsed": 21000, "blockNumber": 100,
                             "effectiveGasPrice": 3 * 10 ** 9}
    assert ba.poll_pending_tx(ctx) is None       # 1 confirmation < 2
    ba.w3.eth.block_number = 101
    out = ba.poll_pending_tx(ctx)
    assert out["status"] == 1 and out["confirmations"] == 2
    assert ctx.blockchain.last_pending_tx_hash is None
    assert abs(ctx.blockchain.total_gas_spent_eth - 21000 * 3e9 / 1e18) < 1e-15


def test_rbf_tracks_all_hashes_and_original_can_confirm():
    ba = make_adapter(blockchain_confirmations=1)
    ctx = v29.BotContext()
    first = ba.send_native(DEST, 0.001, ctx)["tx_hash"]
    ctx.blockchain.last_pending_tx_since = (
        v29._utcnow() - timedelta(seconds=ba.cfg.blockchain_tx_timeout_sec + 5)
    ).isoformat()
    assert ba.poll_pending_tx(ctx) is None        # déclenche un RBF
    assert len(ctx.blockchain.pending_tx_hashes) == 2
    replaced = ba.w3.eth.sent[-1]
    assert replaced["nonce"] == 7
    assert replaced["maxPriorityFeePerGas"] > ba.w3.eth.sent[0]["maxPriorityFeePerGas"]
    # Finalement c'est la tx ORIGINALE qui est minée.
    ba.w3.eth.receipts[first] = {"status": 1, "gasUsed": 21000,
                                 "blockNumber": 100}
    out = ba.poll_pending_tx(ctx)
    assert out["tx_hash"] == first
    assert ctx.blockchain.last_pending_tx_hash is None


def test_rbf_exhausted_keeps_tracking():
    ba = make_adapter()
    ctx = v29.BotContext()
    ba.send_native(DEST, 0.001, ctx)
    ctx.blockchain.rbf_attempts = ba.cfg.blockchain_max_rbf_attempts
    assert ba.check_stuck_tx(ctx) is False
    assert ctx.blockchain.last_pending_tx_hash is not None
    assert ctx.blockchain.rbf_exhausted_notified


def test_pending_guard_and_address_validation_order():
    ba = make_adapter()
    ctx = v29.BotContext()
    ctx.blockchain.last_pending_tx_hash = "0xdead"
    with pytest.raises(v29.BlockchainError, match="Adresse invalide"):
        ba.send_native("pas-une-adresse", 0.001, ctx)
    with pytest.raises(v29.BlockchainError, match="Pending"):
        ba.send_native(DEST, 0.001, ctx)


def test_whitelist_and_value_cap():
    ba = make_adapter()
    with pytest.raises(v29.BlockchainError, match="non autorisée"):
        ba.policy.guard_tx("0x" + "44" * 20, 0.001)
    with pytest.raises(v29.BlockchainError, match="max"):
        ba.policy.guard_tx(DEST, 1.0)


def test_live_send_without_ctx_refused():
    ba = make_adapter()
    with pytest.raises(v29.BlockchainError, match="sans contexte"):
        ba.send_native(DEST, 0.001, None)


def test_exact_wei_conversion():
    assert v29._to_wei_exact(0.1) == 10 ** 17
    assert v29._to_wei_exact(1.000001, 6) == 1_000_001


def test_encode_transfer_compat():
    class Fn:
        def _encode_transaction_data(self):
            return "0xa9059cbb"

    class Functions:
        def transfer(self, to, amount):
            return Fn()

    class Contract:
        functions = Functions()

    assert v29.BlockchainAdapter._encode_transfer(Contract(), DEST, 5) == "0xa9059cbb"
