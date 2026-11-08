from decimal import Decimal

from tradebot.exchange import BinanceError, SymbolRules


def rules(symbol, step="0.001", tick="0.1", min_qty="0.001", min_notional="5"):
    return SymbolRules(symbol, symbol[:-4], "USDT", Decimal(step), Decimal(tick),
                       Decimal(min_qty), Decimal(min_notional))


class FakeFutures:
    def __init__(self, mark=Decimal("67250.1"), available="1000"):
        self.mark = {"BTCUSDT": mark, "ETHUSDT": Decimal("3200")}
        self.available = available
        self.calls = []
        self.fail_on = None  # order type that raises
        self.pos = []

    def rules(self, symbol):
        if symbol not in self.mark:
            raise BinanceError(0, None, f"{symbol} is not a tradable symbol here")
        return rules(symbol, step="0.001" if symbol == "BTCUSDT" else "0.01",
                     tick="0.1" if symbol == "BTCUSDT" else "0.01", min_notional="100")

    def mark_price(self, symbol):
        return self.mark[symbol]

    def balance(self, asset="USDT"):
        return {"asset": asset, "balance": self.available, "available": self.available}

    def set_margin_type(self, symbol, margin_type):
        self.calls.append(("set_margin_type", symbol, margin_type))

    def set_leverage(self, symbol, leverage):
        self.calls.append(("set_leverage", symbol, leverage))
        return {}

    def new_order(self, **p):
        self.calls.append(("new_order", p))
        if self.fail_on and p["type"] == self.fail_on:
            raise BinanceError(400, -2021, "Order would immediately trigger.")
        return {"symbol": p["symbol"], "side": p["side"], "type": p["type"], "status": "FILLED",
                "executedQty": p.get("quantity", "0"), "avgPrice": "67251.0",
                "orderId": len(self.calls)}

    def positions(self):
        return self.pos

    def open_orders(self, symbol=None):
        return []

    def cancel_all(self, symbol):
        self.calls.append(("cancel_all", symbol))
        return {}


class FakeSpot:
    def __init__(self):
        self.calls = []
        self.bal = [{"asset": "USDT", "free": "500", "locked": "0"},
                    {"asset": "BTC", "free": "0.001", "locked": "0"}]

    def rules(self, symbol):
        return rules(symbol, step="0.00001", tick="0.01", min_qty="0.00001", min_notional="5")

    def price(self, symbol):
        return Decimal("67000")

    def balances(self):
        return self.bal

    def open_orders(self, symbol=None):
        return []

    def new_order(self, **p):
        self.calls.append(("new_order", p))
        return {"symbol": p["symbol"], "side": p["side"], "type": p["type"], "status": "FILLED",
                "executedQty": "0.00074", "orderId": 7,
                "fills": [{"price": "67000", "qty": "0.00074"}]}
