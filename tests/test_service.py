from decimal import Decimal

import pytest

from tests.fakes import FakeFutures, FakeSpot
from tradebot.config import Settings
from tradebot.service import TradeService, money
from tradebot.storage import TradeLog


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"))
    f, sp, clock = FakeFutures(), FakeSpot(), Clock()
    svc = TradeService(s, f, sp, TradeLog(s.db_path), clock=clock)
    return svc, f, sp, clock


def test_preview_does_not_touch_order_endpoints(env):
    svc, f, sp, _ = env
    r = svc.preview("long btc 100 5x tp 72k sl 66k")
    assert r.status == "confirm" and r.token
    assert "LONG BTCUSDT · margin 100 USDT · 5x" in r.message
    assert "TP 72,000 · SL 66,000" in r.message and "TESTNET" in r.message
    assert "liquidation" in r.message
    assert f.calls == [] and sp.calls == []


def test_confirm_places_entry_then_tp_sl(env):
    svc, f, _, _ = env
    r = svc.confirm(svc.preview("long btc 100 5x tp 72k sl 66k").token)
    assert r.status == "done"
    assert f.calls[0] == ("set_margin_type", "BTCUSDT", "ISOLATED")
    assert f.calls[1] == ("set_leverage", "BTCUSDT", 5)
    entry, tp, sl = (c[1] for c in f.calls[2:])
    assert entry == {"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.007",
                     "type": "MARKET", "newOrderRespType": "RESULT"}
    assert tp["type"] == "TAKE_PROFIT_MARKET" and tp["side"] == "SELL" and tp["stopPrice"] == "72000"
    assert sl["type"] == "STOP_MARKET" and sl["closePosition"] == "true"
    assert svc.log.recent()[0]["status"] == "ok"


def test_token_single_use_and_expiry(env):
    svc, f, _, clock = env
    tok = svc.preview("long btc 100 5x").token
    assert svc.confirm(tok).status == "done"
    assert svc.confirm(tok).status == "error"
    tok = svc.preview("long btc 100 5x").token
    clock.t += 61
    r = svc.confirm(tok)
    assert r.status == "error" and "expired" in r.message


def test_cancel(env):
    svc, f, _, _ = env
    tok = svc.preview("long btc 100 5x").token
    assert svc.cancel(tok).status == "done"
    assert svc.confirm(tok).status == "error"
    assert f.calls == []


def test_limit_short(env):
    svc, f, _, _ = env
    r = svc.preview("short eth 50 3x at 3300.555 tp 2900 sl 3400")
    assert r.status == "confirm", r.message
    svc.confirm(r.token)
    entry = f.calls[2][1]
    assert entry["type"] == "LIMIT" and entry["price"] == "3300.55" and entry["side"] == "SELL"
    assert entry["quantity"] == "0.04"  # 150 / 3300.55 floored to 0.01


@pytest.mark.parametrize("text,msg", [
    ("long doge 100 5x", "not on the whitelist"),
    ("long btc 100 50x", "Leverage must be"),
    ("long btc 40 2x", "below Binance's minimum"),
        ("long btc 10 2x", "Too small"),
    ("long btc 100 5x tp 60000", "TP 60,000 must be above"),
    ("long btc 100 5x sl 70000", "SL 70,000 must be below"),
    ("short btc 100 5x tp 70000", "TP 70,000 must be below"),
    ("long btc 5000 5x", "Not enough futures USDT"),
    ("long btc 100", "What leverage"),
    ("buy btc 1000", "Not enough spot USDT"),
    ("sell btc 100", "Not enough BTC"),
    ("close eth", "No open ETH futures position"),
])
def test_rejections(env, text, msg):
    svc, f, sp, _ = env
    r = svc.preview(text)
    assert r.status in {"error", "incomplete"} and msg in r.message
    assert f.calls == [] and sp.calls == []


def test_spot_market_buy_uses_quote_qty(env):
    svc, _, sp, _ = env
    r = svc.preview("buy btc 50")
    assert "SPOT BUY BTCUSDT · 50 USDT" in r.message
    done = svc.confirm(r.token)
    assert sp.calls[0][1] == {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                              "quoteOrderQty": "50", "newOrderRespType": "FULL"}
    assert "price 67000" in done.message


def test_close_position(env):
    svc, f, _, _ = env
    f.pos = [{"symbol": "BTCUSDT", "positionAmt": "-0.010", "unRealizedProfit": "-3.2",
              "entryPrice": "67000", "markPrice": "67320", "leverage": "5"}]
    r = svc.preview("close all")
    assert "BTCUSDT SHORT 0.01" in r.message
    svc.confirm(r.token)
    assert f.calls[0] == ("cancel_all", "BTCUSDT")
    assert f.calls[1][1] | {} == {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                                  "quantity": "0.01", "reduceOnly": "true",
                                  "newOrderRespType": "RESULT"}


def test_tp_failure_after_entry_is_reported_not_hidden(env):
    svc, f, _, _ = env
    f.fail_on = "TAKE_PROFIT_MARKET"
    r = svc.confirm(svc.preview("long btc 100 5x tp 72k sl 66k").token)
    assert r.status == "done"
    assert "NOT placed" in r.message
    assert [c[1]["type"] for c in f.calls if c[0] == "new_order"] == [
        "MARKET", "TAKE_PROFIT_MARKET", "STOP_MARKET"]
    assert svc.log.recent()[0]["status"] == "partial"


def test_entry_failure(env):
    svc, f, _, _ = env
    f.fail_on = "MARKET"
    r = svc.confirm(svc.preview("long btc 100 5x tp 72k").token)
    assert r.status == "error" and "Nothing was placed" in r.message
    assert svc.log.recent()[0]["status"] == "failed"


def test_queries_need_no_confirmation(env):
    svc, f, _, _ = env
    assert svc.preview("positions").message == "No open futures positions."
    assert "Futures: 1,000 USDT" in svc.preview("balance").message


def test_money():
    assert money(Decimal("72000")) == "72,000"
    assert money(Decimal("67250.1")) == "67,250.10"
    assert money(Decimal("100")) == "100"
    assert money(Decimal("0.5")) == "0.5"
    assert money(Decimal("470.6790564")) == "470.68"
