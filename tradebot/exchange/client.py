"""Thin, signed REST clients for Binance spot and USDⓈ-M futures.

Kept small on purpose: only the endpoints the bot uses, so every request that
can move money is easy to read and audit.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceError(Exception):
    def __init__(self, status: int, code: int | None, msg: str):
        super().__init__(f"Binance error {code} (HTTP {status}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    step_size: Decimal
    tick_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def round_qty(self, qty: Decimal | float) -> Decimal:
        return _floor_to(Decimal(str(qty)), self.step_size)

    def round_price(self, price: Decimal | float) -> Decimal:
        return _floor_to(Decimal(str(price)), self.tick_size)


def _floor_to(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step.normalize()


def fmt(d: Decimal | float) -> str:
    """Decimal to the plain string Binance expects (no exponent, no trailing zeros)."""
    s = format(Decimal(str(d)).normalize(), "f")
    return s


def _parse_rules(info: dict) -> SymbolRules:
    filters = {f["filterType"]: f for f in info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    price = filters.get("PRICE_FILTER", {})
    notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    min_notional = notional.get("notional") or notional.get("minNotional") or "0"
    return SymbolRules(
        symbol=info["symbol"],
        base_asset=info["baseAsset"],
        quote_asset=info["quoteAsset"],
        step_size=Decimal(lot.get("stepSize", "0")),
        tick_size=Decimal(price.get("tickSize", "0")),
        min_qty=Decimal(lot.get("minQty", "0")),
        min_notional=Decimal(min_notional),
    )


class _BaseClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str,
                 recv_window_ms: int = 5000, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window_ms = recv_window_ms
        self.http = http or httpx.Client(timeout=10)
        self._rules: dict[str, SymbolRules] | None = None

    def _request(self, method: str, path: str, params: dict | None = None,
                 signed: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise BinanceError(0, None, "API key/secret not configured (see .env.example)")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window_ms
            query = urlencode(params)
            sig = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            query += f"&signature={sig}"
            headers["X-MBX-APIKEY"] = self.api_key
        else:
            query = urlencode(params)
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        resp = self.http.request(method, url, headers=headers)
        try:
            data = resp.json()
        except ValueError:
            data = {"msg": resp.text}
        if resp.status_code >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", resp.text) if isinstance(data, dict) else resp.text
            raise BinanceError(resp.status_code, code, msg)
        return data

    _exchange_info_path = ""

    def rules(self, symbol: str) -> SymbolRules:
        if self._rules is None:
            info = self._request("GET", self._exchange_info_path)
            self._rules = {s["symbol"]: _parse_rules(s) for s in info["symbols"]
                           if s.get("status") == "TRADING"}
        if symbol not in self._rules:
            raise BinanceError(0, None, f"{symbol} is not a tradable symbol here")
        return self._rules[symbol]


class FuturesClient(_BaseClient):
    """USDⓈ-M futures (fapi)."""

    _exchange_info_path = "/fapi/v1/exchangeInfo"

    def mark_price(self, symbol: str) -> Decimal:
        data = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return Decimal(data["markPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request("POST", "/fapi/v1/leverage",
                             {"symbol": symbol, "leverage": leverage}, signed=True)

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        try:
            self._request("POST", "/fapi/v1/marginType",
                          {"symbol": symbol, "marginType": margin_type}, signed=True)
        except BinanceError as e:
            if e.code != -4046:  # "No need to change margin type."
                raise

    def new_order(self, **params) -> dict:
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def positions(self) -> list[dict]:
        data = self._request("GET", "/fapi/v2/positionRisk", signed=True)
        return [p for p in data if Decimal(p["positionAmt"]) != 0]

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        return self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)

    def cancel_all(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def balance(self, asset: str = "USDT") -> dict:
        data = self._request("GET", "/fapi/v2/balance", signed=True)
        for row in data:
            if row["asset"] == asset:
                return {"asset": asset, "balance": row["balance"],
                        "available": row["availableBalance"]}
        return {"asset": asset, "balance": "0", "available": "0"}


class SpotClient(_BaseClient):
    """Spot (api/v3)."""

    _exchange_info_path = "/api/v3/exchangeInfo"

    def price(self, symbol: str) -> Decimal:
        data = self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return Decimal(data["price"])

    def new_order(self, **params) -> dict:
        return self._request("POST", "/api/v3/order", params, signed=True)

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        return self._request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True)

    def balances(self) -> list[dict]:
        data = self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        return [b for b in data["balances"]
                if Decimal(b["free"]) != 0 or Decimal(b["locked"]) != 0]
