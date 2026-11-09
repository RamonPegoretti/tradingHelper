"""Regression tests for the bugs found in the November 2026 debug pass."""
from decimal import Decimal

import httpx
import pytest
import respx

from tests.fakes import FakeFutures, FakeSpot
from tradebot.auth import AuthManager, hash_password, new_totp_secret, totp
from tradebot.config import Settings
from tradebot.exchange import BinanceError, FuturesClient
from tradebot.exchange.demo import DemoFutures
from tradebot.models import FuturesOrder
from tradebot.parser import parse
from tradebot.service import TradeService
from tradebot.storage import TradeLog


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"))
    f, sp = FakeFutures(), FakeSpot()
    return TradeService(s, f, sp, TradeLog(s.db_path)), f, sp


# ---- auth -------------------------------------------------------------------

def test_code_with_space_cannot_replay():
    secret = new_totp_secret()
    now = 1_800_000_000.0
    auth = AuthManager(hash_password("correct horse battery"), secret, clock=lambda: now)
    code = totp(secret, now)
    assert auth.login("correct horse battery", code)
    assert auth.login("correct horse battery", f"{code[:3]} {code[3:]}") is None
    assert auth.login("correct horse battery", f" {code} ") is None


# ---- parser: Swiss / European number formats ---------------------------------

@pytest.mark.parametrize("text,amount,tp", [
    ("long btc 12,5 2x", Decimal("12.5"), None),
    ("long btc 100 5x tp 72'000", Decimal(100), Decimal(72000)),
    ("long btc 100 5x tp 72’000", Decimal(100), Decimal(72000)),
    ("long btc 100 5x tp 72,000", Decimal(100), Decimal(72000)),
    ("long btc 100 5x tp 72000,5", Decimal(100), Decimal("72000.5")),
])
def test_number_formats(text, amount, tp):
    c = parse(text)
    assert isinstance(c, FuturesOrder) and c.amount == amount and c.tp == tp


# ---- report / query formatting ----------------------------------------------

def test_report_shows_trigger_price_for_tp_sl(env):
    svc, f, _ = env
    real_new_order = f.new_order

    def binance_like(**p):  # Binance answers TP/SL with price "0" and origQty "0"
        r = real_new_order(**p)
        if "stopPrice" in p:
            r |= {"avgPrice": "0", "price": "0", "origQty": "0", "executedQty": "0",
                  "stopPrice": p["stopPrice"], "status": "NEW"}
        return r

    f.new_order = binance_like
    r = svc.confirm(svc.preview("long btc 100 5x tp 72k sl 66k").token)
    assert "trigger 72000" in r.message and "trigger 66000" in r.message
    assert "price 0 " not in r.message and "whole position" in r.message


def test_orders_and_positions_text_skip_zero_fields(env):
    svc, f, _ = env
    f.orders = [{"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "origQty": "0",
                 "price": "0", "stopPrice": "66000", "closePosition": True, "orderId": 5}]
    msg = svc.preview("orders").message
    assert "trigger 66,000" in msg and "price 0" not in msg and "whole position" in msg
    f.pos = [{"symbol": "BTCUSDT", "positionAmt": "0.007", "entryPrice": "67000",
              "markPrice": "67100", "unRealizedProfit": "0.7", "leverage": "5",
              "liquidationPrice": "0"}]
    msg = svc.preview("positions").message
    assert "liq" not in msg and "uPnL +0.70" in msg


# ---- close cancels leftover orders ------------------------------------------

def test_close_cancels_orders_without_position(env):
    svc, f, _ = env
    f.orders = [{"symbol": "ETHUSDT", "side": "SELL", "type": "LIMIT", "origQty": "0.045",
                 "price": "3300", "orderId": 9}]
    r = svc.preview("close eth")
    assert r.status == "confirm" and r.message.startswith("CANCEL orders: ETHUSDT")
    done = svc.confirm(r.token)
    assert done.status == "done" and done.message == "Open orders cancelled."
    assert f.calls == [("cancel_all", "ETHUSDT")]
    f.orders = []
    assert "No open ETH futures position or order" in svc.preview("close eth").message


# ---- spot sell the whole holding --------------------------------------------

def test_spot_sell_slightly_more_than_held_sells_all(env):
    svc, _, sp = env
    sp.bal = [{"asset": "USDT", "free": "0", "locked": "0"},
              {"asset": "BTC", "free": "0.00074", "locked": "0"}]   # ≈ 49.58 USDT at 67,000
    r = svc.preview("sell btc 50")
    assert r.status == "confirm" and "all 0.00074 BTC" in r.message
    svc.confirm(r.token)
    p = sp.calls[0][1]
    assert p["quantity"] == "0.00074" and "quoteOrderQty" not in p
    assert svc.preview("sell btc 60").status == "error"   # far more than held: still refused


# ---- network errors become readable errors ----------------------------------

@respx.mock
def test_network_error_is_a_binance_error(env):
    respx.post("https://x.test/fapi/v1/order").mock(side_effect=httpx.ConnectTimeout("boom"))
    c = FuturesClient("https://x.test", "k", "s")
    with pytest.raises(BinanceError, match="network error"):
        c.new_order(symbol="BTCUSDT", side="BUY", type="MARKET", quantity="0.007")


# ---- demo exchange bookkeeping ----------------------------------------------

def test_demo_averages_entry_and_realises_pnl(monkeypatch):
    import tradebot.exchange.demo as demo
    prices = iter([Decimal("100"), Decimal("200"), Decimal("250")])
    monkeypatch.setattr(demo, "_price", lambda sym: next(prices))
    d = DemoFutures()
    d.new_order(symbol="SOLUSDT", side="BUY", type="MARKET", quantity="1")
    d.new_order(symbol="SOLUSDT", side="BUY", type="MARKET", quantity="1")
    assert Decimal(d.pos["SOLUSDT"]["entryPrice"]) == 150
    d.new_order(symbol="SOLUSDT", side="SELL", type="MARKET", quantity="2", reduceOnly="true")
    assert "SOLUSDT" not in d.pos
    assert d.wallet == Decimal("10200")     # (250 - 150) * 2


# ---- Windows: .env saved with a BOM ------------------------------------------

def test_env_file_with_bom(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes("﻿TRADEBOT_ENV=demo\nTRADEBOT_MAX_LEVERAGE=3\n".encode("utf-8"))
    s = Settings(_env_file=str(p))
    assert s.env == "demo" and s.max_leverage == 3


def test_log_limit_is_clamped(tmp_path):
    log = TradeLog(str(tmp_path / "t.db"))
    for i in range(3):
        log.add(env="demo", text=str(i), command={}, summary="", status="ok")
    import tradebot.api as api
    from fastapi.testclient import TestClient
    secret = new_totp_secret()
    auth = AuthManager(hash_password("correct horse battery"), secret)
    svc = TradeService(Settings(_env_file=None, env="demo"), FakeFutures(), FakeSpot(), log)
    api.app.dependency_overrides[api.get_auth] = lambda: auth
    api.app.dependency_overrides[api.get_service] = lambda: svc
    try:
        c = TestClient(api.app)
        import time
        c.post("/api/login", json={"password": "correct horse battery",
                                   "code": totp(secret, time.time())}, headers={"X-Tradebot": "1"})
        assert len(c.get("/api/log?limit=-1").json()) == 1
    finally:
        api.app.dependency_overrides.clear()


# ---- warn when an order changes an open position (one-way mode) -------------

def _btc_long(f, amt="0.007"):
    f.pos = [{"symbol": "BTCUSDT", "positionAmt": amt, "entryPrice": "67000",
              "markPrice": "67100", "unRealizedProfit": "0.7", "leverage": "5"}]


def test_short_against_open_long_warns_it_closes(env):
    svc, f, _ = env
    _btc_long(f)
    f.orders = [{"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "origQty": "0",
                 "price": "0", "stopPrice": "66000", "orderId": 3}]
    r = svc.preview("short btc 100 5x")       # 0.007 BTC, same size as the long
    assert r.status == "confirm"
    assert r.message.startswith("⚠️ This CLOSES your open LONG 0.007 BTC")
    assert "1 open TP/SL/limit order stay open" in r.message


def test_position_effect_reduce_flip_add(env):
    svc, f, _ = env
    _btc_long(f, "0.014")
    assert "REDUCES your open LONG 0.014 BTC by 0.007" in svc.preview("short btc 100 5x").message
    _btc_long(f, "0.003")
    assert "opens a SHORT of 0.004 BTC" in svc.preview("short btc 100 5x").message
    assert svc.preview("long btc 100 5x").message.startswith("ℹ️ Adds to your open LONG")
    f.pos = []
    assert "⚠️" not in svc.preview("short btc 100 5x").message
