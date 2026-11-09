"""In-memory fake exchange for TRADEBOT_ENV=demo: try the app with no Binance keys.

Prices are fixed-ish (small random drift), orders fill instantly, nothing leaves the machine.
"""
from __future__ import annotations

import random
from decimal import Decimal

from .client import BinanceError, SymbolRules

PRICES = {"BTC": "67250", "ETH": "3200", "SOL": "145", "BNB": "590", "XRP": "0.52",
          "DOGE": "0.12", "ADA": "0.45", "AVAX": "28"}


def _rules(sym: str, futures: bool) -> SymbolRules:
    base = sym[:-4]
    if base not in PRICES:
        raise BinanceError(0, None, f"{sym} is not a tradable symbol here")
    p = Decimal(PRICES[base])
    step = Decimal("0.001") if p > 1000 else Decimal("0.01") if p > 10 else Decimal("1")
    tick = Decimal("0.1") if p > 1000 else Decimal("0.01") if p > 1 else Decimal("0.0001")
    if not futures:
        step = step / 100
    return SymbolRules(sym, base, "USDT", step, tick, step,
                       Decimal("100") if futures else Decimal("5"))


def _price(sym: str) -> Decimal:
    p = Decimal(PRICES[sym[:-4]])
    return (p * Decimal(str(1 + random.uniform(-0.001, 0.001)))).quantize(Decimal("0.0001"))


class _Orders:
    def __init__(self):
        self._id = 1000
        self.open: list[dict] = []

    def next_id(self) -> int:
        self._id += 1
        return self._id


class DemoFutures:
    def __init__(self):
        self.wallet = Decimal("10000")
        self.pos: dict[str, dict] = {}
        self.lev: dict[str, int] = {}
        self.o = _Orders()

    def rules(self, symbol):
        return _rules(symbol, True)

    def mark_price(self, symbol):
        self.rules(symbol)
        return _price(symbol)

    def set_margin_type(self, symbol, margin_type):
        pass

    def set_leverage(self, symbol, leverage):
        self.lev[symbol] = leverage
        return {}

    def new_order(self, **p):
        sym, side, typ = p["symbol"], p["side"], p["type"]
        oid = self.o.next_id()
        if typ != "MARKET":
            order = {"symbol": sym, "side": side, "type": typ, "status": "NEW", "orderId": oid,
                     "origQty": p.get("quantity", "0"), "price": p.get("price", "0"),
                     "stopPrice": p.get("stopPrice", "0")}
            self.o.open.append(order)
            return order
        qty = Decimal(p["quantity"]) * (1 if side == "BUY" else -1)
        price = _price(sym)
        cur = self.pos.get(sym)
        amt = (Decimal(cur["positionAmt"]) if cur else Decimal(0)) + qty
        if amt == 0:
            self.pos.pop(sym, None)
        else:
            entry = cur["entryPrice"] if cur and (Decimal(cur["positionAmt"]) > 0) == (amt > 0) else str(price)
            self.pos[sym] = {"symbol": sym, "positionAmt": str(amt), "entryPrice": entry,
                             "leverage": str(self.lev.get(sym, 1))}
        return {"symbol": sym, "side": side, "type": typ, "status": "FILLED", "orderId": oid,
                "executedQty": p["quantity"], "avgPrice": str(price)}

    def positions(self):
        out = []
        for p in self.pos.values():
            mark = _price(p["symbol"])
            amt, entry = Decimal(p["positionAmt"]), Decimal(p["entryPrice"])
            out.append(p | {"markPrice": str(mark), "unRealizedProfit": str(((mark - entry) * amt).quantize(Decimal("0.01"))),
                            "liquidationPrice": "0"})
        return out

    def open_orders(self, symbol=None):
        return [o for o in self.o.open if symbol is None or o["symbol"] == symbol]

    def cancel_all(self, symbol):
        self.o.open = [o for o in self.o.open if o["symbol"] != symbol]
        return {}

    def balance(self, asset="USDT"):
        used = sum(abs(Decimal(p["positionAmt"])) * Decimal(p["entryPrice"]) / Decimal(p["leverage"])
                   for p in self.pos.values())
        return {"asset": asset, "balance": str(self.wallet), "available": str(self.wallet - used)}


class DemoSpot:
    def __init__(self):
        self.bal = {"USDT": Decimal("5000")}
        self.o = _Orders()

    def rules(self, symbol):
        return _rules(symbol, False)

    def price(self, symbol):
        self.rules(symbol)
        return _price(symbol)

    def balances(self):
        return [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.bal.items() if v]

    def open_orders(self, symbol=None):
        return [o for o in self.o.open if symbol is None or o["symbol"] == symbol]

    def new_order(self, **p):
        sym, side, typ = p["symbol"], p["side"], p["type"]
        oid = self.o.next_id()
        if typ != "MARKET":
            order = {"symbol": sym, "side": side, "type": typ, "status": "NEW", "orderId": oid,
                     "origQty": p["quantity"], "price": p["price"]}
            self.o.open.append(order)
            return order
        price = _price(sym)
        quote = Decimal(p["quoteOrderQty"])
        qty = (quote / price).quantize(Decimal("0.00001"))
        base = sym[:-4]
        sign = 1 if side == "BUY" else -1
        self.bal["USDT"] = self.bal.get("USDT", Decimal(0)) - sign * quote
        self.bal[base] = self.bal.get(base, Decimal(0)) + sign * qty
        return {"symbol": sym, "side": side, "type": typ, "status": "FILLED", "orderId": oid,
                "executedQty": str(qty), "fills": [{"price": str(price), "qty": str(qty)}]}
