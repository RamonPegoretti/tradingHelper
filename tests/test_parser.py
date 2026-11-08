from decimal import Decimal

import pytest

from tradebot.models import ClosePosition, FuturesOrder, Incomplete, Query, SpotOrder
from tradebot.parser import ParseError, parse


def test_market_long():
    c = parse("long btc 100 5x")
    assert c == FuturesOrder(side="long", coin="BTC", amount=Decimal(100), leverage=5)


def test_short_with_tp_sl():
    c = parse("short eth 50 10x tp 2900 sl 3400")
    assert isinstance(c, FuturesOrder)
    assert (c.side, c.coin, c.amount, c.leverage, c.tp, c.sl) == (
        "short", "ETH", Decimal(50), 10, Decimal(2900), Decimal(3400))


def test_limit_and_k_suffix_and_usdt():
    c = parse("long BTC 100 usdt 5x at 68k tp 72k sl 66.5k")
    assert c.price == Decimal(68000) and c.tp == Decimal(72000) and c.sl == Decimal(66500)


@pytest.mark.parametrize("text", [
    "long sol 30 3x at 140", "long sol 30 3x @140", "long solusdt $30 x3 @ 140",
    "long 30usdt sol 3x limit 140", "LONG SOL 30 3 x at 140",
])
def test_variants(text):
    c = parse(text)
    assert (c.coin, c.amount, c.leverage, c.price) == ("SOL", Decimal(30), 3, Decimal(140))


def test_thousands_comma():
    assert parse("long btc 1,000 2x tp 72,000").tp == Decimal(72000)


def test_spot():
    assert parse("buy btc 50") == SpotOrder(side="buy", coin="BTC", amount=Decimal(50))
    assert parse("buy 50 usdt btc") == SpotOrder(side="buy", coin="BTC", amount=Decimal(50))
    assert parse("sell eth 20 at 4000").price == Decimal(4000)


def test_close_and_queries():
    assert parse("close btc") == ClosePosition(coin="BTC")
    assert parse("close all") == ClosePosition(coin=None)
    assert parse("positions") == Query(what="positions")
    assert parse("balance") == Query(what="balance")
    assert parse("open orders") == Query(what="orders")


def test_missing_pieces_ask_instead_of_guessing():
    r = parse("long btc 100")
    assert isinstance(r, Incomplete) and r.missing == ["leverage"]
    r = parse("short eth 5x")
    assert r.missing == ["amount"]
    r = parse("buy 50")
    assert r.missing == ["coin"]


@pytest.mark.parametrize("text,msg", [
    ("long btc 100 chf 5x", "USDT only"),
    ("buy btc 50 3x", "no leverage"),
    ("long btc 100 200 5x", "Two amounts"),
    ("long btc eth 100 5x", "Two coins"),
    ("long btc 100 5x tp", "needs a price"),
    ("deposit usdt", "milestone 7"),
    ("transfer 100 usdt to futures", "milestone 7"),
    ("withdraw 200 usdt to ledger", "phase 2"),
    ("hello", "Unknown"),
    ("", "Empty"),
])
def test_errors(text, msg):
    with pytest.raises(ParseError, match=msg):
        parse(text)
