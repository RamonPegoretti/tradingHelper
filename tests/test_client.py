import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import respx

from tradebot.exchange import BinanceError, FuturesClient

BASE = "https://testnet.binancefuture.com"


@respx.mock
def test_signed_request_has_valid_signature():
    route = respx.post(f"{BASE}/fapi/v1/order").mock(
        return_value=httpx.Response(200, json={"orderId": 1}))
    c = FuturesClient(BASE, "key", "secret")
    c.new_order(symbol="BTCUSDT", side="BUY", type="MARKET", quantity="0.007")
    req = route.calls[0].request
    assert req.headers["X-MBX-APIKEY"] == "key"
    query = urlsplit(str(req.url)).query
    body, sig = query.rsplit("&signature=", 1)
    assert hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest() == sig
    params = dict(parse_qsl(body))
    assert params["symbol"] == "BTCUSDT" and "timestamp" in params


@respx.mock
def test_error_mapping_and_margin_type_noop():
    respx.post(f"{BASE}/fapi/v1/marginType").mock(
        return_value=httpx.Response(400, json={"code": -4046, "msg": "No need to change margin type."}))
    respx.post(f"{BASE}/fapi/v1/leverage").mock(
        return_value=httpx.Response(400, json={"code": -4028, "msg": "Leverage 200 is not valid"}))
    c = FuturesClient(BASE, "key", "secret")
    c.set_margin_type("BTCUSDT", "ISOLATED")  # swallowed
    with pytest.raises(BinanceError) as e:
        c.set_leverage("BTCUSDT", 200)
    assert e.value.code == -4028


def test_missing_keys():
    with pytest.raises(BinanceError, match="not configured"):
        FuturesClient(BASE, "", "").positions()


@respx.mock
def test_rules_rounding():
    respx.get(f"{BASE}/fapi/v1/exchangeInfo").mock(return_value=httpx.Response(200, json={
        "symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
                     "filters": [
                         {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                         {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                         {"filterType": "MIN_NOTIONAL", "notional": "100"}]}]}))
    r = FuturesClient(BASE, "k", "s").rules("BTCUSDT")
    assert r.round_qty(Decimal("0.0074")) == Decimal("0.007")
    assert r.round_price(Decimal("67250.19")) == Decimal("67250.1")
    assert r.min_notional == Decimal(100)
