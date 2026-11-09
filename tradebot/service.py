"""Parse → validate → summary → confirm → place → report.

Every interface (CLI, web app, MCP) goes through TradeService, and the only way
to reach the exchange's order endpoints is confirm(token) with a token from
preview() that hasn't expired. That's what makes the confirm step unskippable.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Literal

from .config import Settings
from .exchange import BinanceError, FuturesClient, SpotClient
from .exchange.client import fmt
from .models import ClosePosition, Command, FuturesOrder, Incomplete, Query, SpotOrder
from .parser import ParseError, parse
from .storage import TradeLog

# Slack so a 100 USDT margin isn't rejected for missing a few cents of fees.
FEE_BUFFER = Decimal("1.002")
# "sell btc 50" sells the whole holding if it's worth at least 97% of 50.
SELL_ALL_SLACK = Decimal("0.97")


class ValidationError(ValueError):
    pass


@dataclass
class Plan:
    """A validated command plus the exact exchange calls confirm() will make."""
    command: Command
    text: str
    summary: str
    steps: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class Pending:
    plan: Plan
    expires_at: float


@dataclass
class Reply:
    status: Literal["confirm", "incomplete", "info", "error", "done"]
    message: str
    token: str | None = None
    expires_at: float | None = None
    data: Any = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def money(x: Decimal | float) -> str:
    """72000 -> '72,000', 67250.1 -> '67,250.10', 0.0123456789 -> '0.01234568'."""
    x = Decimal(str(x))
    if abs(x) >= 1000:
        s = f"{x:,.2f}"
        return s[:-3] if s.endswith(".00") else s
    if x == x.to_integral_value():
        return f"{x:.0f}"
    if abs(x) >= 1:
        return f"{x:.2f}"
    return fmt(x.quantize(Decimal("0.00000001")))


def _nonzero(x: Any) -> str | None:
    """Binance sends "0" / "0.00000" for fields that don't apply; treat those as missing."""
    if x in (None, ""):
        return None
    try:
        return str(x) if Decimal(str(x)) != 0 else None
    except ArithmeticError:
        return None


class TradeService:
    def __init__(self, settings: Settings, futures: FuturesClient, spot: SpotClient,
                 log: TradeLog, clock: Callable[[], float] = time.time):
        self.s = settings
        self.futures = futures
        self.spot = spot
        self.log = log
        self.clock = clock
        self._pending: dict[str, Pending] = {}

    # ---- entry points -------------------------------------------------------

    def preview(self, text: str) -> Reply:
        try:
            cmd = parse(text)
        except ParseError as e:
            return Reply("error", str(e))
        if isinstance(cmd, Incomplete):
            return Reply("incomplete", cmd.question, data=cmd.model_dump())
        return self.preview_command(cmd, text)

    def preview_command(self, cmd: Command, text: str = "") -> Reply:
        """Also the entry point for already-structured commands (Claude, Ollama)."""
        try:
            if isinstance(cmd, Query):
                message, data = self._query(cmd.what)
                return Reply("info", message, data=data)
            if isinstance(cmd, FuturesOrder):
                plan = self._plan_futures(cmd, text)
            elif isinstance(cmd, SpotOrder):
                plan = self._plan_spot(cmd, text)
            elif isinstance(cmd, ClosePosition):
                plan = self._plan_close(cmd, text)
            else:  # pragma: no cover
                return Reply("error", "Unsupported command")
        except (ValidationError, BinanceError) as e:
            return Reply("error", str(e))

        self._expire()
        token = secrets.token_urlsafe(12)
        expires = self.clock() + self.s.confirm_ttl_seconds
        self._pending[token] = Pending(plan, expires)
        return Reply("confirm", plan.summary, token=token, expires_at=expires,
                     data=cmd.model_dump(mode="json"))

    def confirm(self, token: str) -> Reply:
        pending = self._pending.pop(token, None)
        if pending is None:
            return Reply("error", "Nothing to confirm (unknown or already used).")
        if self.clock() > pending.expires_at:
            return Reply("error", f"Confirmation expired after {self.s.confirm_ttl_seconds}s; "
                                  "preview the order again so the price is fresh.")
        return self._execute(pending.plan)

    def cancel(self, token: str) -> Reply:
        if self._pending.pop(token, None) is None:
            return Reply("error", "Nothing to cancel.")
        return Reply("done", "Cancelled, nothing was sent.")

    # ---- validation + planning ---------------------------------------------

    def _check_coin(self, coin: str) -> None:
        if coin not in self.s.whitelist:
            raise ValidationError(f"{coin} is not on the whitelist ({', '.join(self.s.whitelist)}).")

    def _plan_futures(self, o: FuturesOrder, text: str) -> Plan:
        self._check_coin(o.coin)
        if not 1 <= o.leverage <= self.s.max_leverage:
            raise ValidationError(f"Leverage must be 1–{self.s.max_leverage}x (got {o.leverage}x).")
        rules = self.futures.rules(o.symbol)
        mark = self.futures.mark_price(o.symbol)
        entry = rules.round_price(o.price) if o.price else mark
        if o.price and entry <= 0:
            raise ValidationError("Limit price is below the tick size.")

        notional = o.amount * o.leverage
        qty = rules.round_qty(notional / entry)
        if qty <= 0 or qty < rules.min_qty:
            raise ValidationError(f"Too small: {money(notional)} USDT buys less than the minimum "
                                  f"{fmt(rules.min_qty)} {o.coin}.")
        real_notional = qty * entry
        if real_notional < rules.min_notional:
            raise ValidationError(f"Position {money(real_notional)} USDT is below Binance's minimum "
                                  f"of {money(rules.min_notional)} USDT.")

        long = o.side == "long"
        for name, level, must_be_above in (("TP", o.tp, long), ("SL", o.sl, not long)):
            if level is None:
                continue
            if must_be_above and level <= entry:
                raise ValidationError(f"{name} {money(level)} must be above the entry "
                                      f"≈ {money(entry)} for a {o.side}.")
            if not must_be_above and level >= entry:
                raise ValidationError(f"{name} {money(level)} must be below the entry "
                                      f"≈ {money(entry)} for a {o.side}.")

        bal = self.futures.balance("USDT")
        available = Decimal(bal["available"])
        if available < o.amount * FEE_BUFFER:
            raise ValidationError(f"Not enough futures USDT: need ~{money(o.amount)}, "
                                  f"available {money(available)}.")

        liq = self._liq_estimate(entry, o.leverage, long)
        entry_side, exit_side = ("BUY", "SELL") if long else ("SELL", "BUY")
        steps: list[tuple[str, dict]] = [
            ("futures.set_margin_type", {"symbol": o.symbol, "margin_type": self.s.margin_type}),
            ("futures.set_leverage", {"symbol": o.symbol, "leverage": o.leverage}),
        ]
        entry_params = {"symbol": o.symbol, "side": entry_side, "quantity": fmt(qty)}
        if o.price:
            entry_params |= {"type": "LIMIT", "price": fmt(entry), "timeInForce": "GTC"}
        else:
            entry_params |= {"type": "MARKET", "newOrderRespType": "RESULT"}
        steps.append(("futures.new_order", entry_params))
        for kind, level in (("TAKE_PROFIT_MARKET", o.tp), ("STOP_MARKET", o.sl)):
            if level is not None:
                steps.append(("futures.new_order", {
                    "symbol": o.symbol, "side": exit_side, "type": kind,
                    "stopPrice": fmt(rules.round_price(level)), "closePosition": "true",
                    "workingType": "MARK_PRICE",
                }))

        parts = [
            f"{o.side.upper()} {o.symbol}",
            f"margin {money(o.amount)} USDT",
            f"{o.leverage}x",
            f"position ≈ {money(real_notional)} USDT ({fmt(qty)} {o.coin})",
            f"entry {'limit ' + money(entry) if o.price else '≈ market ' + money(mark)}",
        ]
        if o.tp:
            parts.append(f"TP {money(o.tp)}")
        if o.sl:
            parts.append(f"SL {money(o.sl)}")
        parts.append(f"est. liquidation ≈ {money(liq)}")
        parts.append(self.s.margin_type.lower())
        return Plan(o, text, self._tag(" · ".join(parts)), steps)

    def _liq_estimate(self, entry: Decimal, lev: int, long: bool) -> Decimal:
        """Isolated-margin approximation; Binance's real number depends on tiers and fees."""
        mmr = Decimal(str(self.s.maint_margin_rate))
        inv = Decimal(1) / Decimal(lev)
        if long:
            return entry * (1 - inv) / (1 - mmr)
        return entry * (1 + inv) / (1 + mmr)

    def _plan_spot(self, o: SpotOrder, text: str) -> Plan:
        self._check_coin(o.coin)
        rules = self.spot.rules(o.symbol)
        last = self.spot.price(o.symbol)
        ref = rules.round_price(o.price) if o.price else last
        if o.amount < rules.min_notional:
            raise ValidationError(f"{money(o.amount)} USDT is below Binance's minimum of "
                                  f"{money(rules.min_notional)} USDT.")
        balances = {b["asset"]: Decimal(b["free"]) for b in self.spot.balances()}
        if o.side == "buy":
            have = balances.get("USDT", Decimal(0))
            if have < o.amount:
                raise ValidationError(f"Not enough spot USDT: need {money(o.amount)}, have {money(have)}.")
        sell_all_qty = None
        if o.side == "sell":
            held = balances.get(o.coin, Decimal(0))
            have = held * ref
            if have < o.amount:
                # Bought 50 USDT, price dipped a little, "sell 50" should sell it all, not fail.
                if have >= o.amount * SELL_ALL_SLACK and rules.round_qty(held) > 0:
                    sell_all_qty = rules.round_qty(held)
                else:
                    raise ValidationError(f"Not enough {o.coin} on spot: worth ≈ {money(have)} USDT, "
                                          f"selling {money(o.amount)}.")
        side = o.side.upper()
        if sell_all_qty is not None:
            if sell_all_qty * ref < rules.min_notional:
                raise ValidationError(f"Your {o.coin} is worth ≈ {money(sell_all_qty * ref)} USDT, "
                                      f"below Binance's minimum of {money(rules.min_notional)} USDT.")
            params = {"symbol": o.symbol, "side": side, "quantity": fmt(sell_all_qty)}
            if o.price:
                params |= {"type": "LIMIT", "timeInForce": "GTC", "price": fmt(ref)}
                what = f"all {fmt(sell_all_qty)} {o.coin} limit {money(ref)}"
            else:
                params |= {"type": "MARKET", "newOrderRespType": "FULL"}
                what = f"all {fmt(sell_all_qty)} {o.coin} (≈ {money(sell_all_qty * last)} USDT) at market ≈ {money(last)}"
        elif o.price:
            qty = rules.round_qty(o.amount / ref)
            if qty < rules.min_qty or qty <= 0:
                raise ValidationError(f"Too small for {o.coin}'s minimum quantity {fmt(rules.min_qty)}.")
            params = {"symbol": o.symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
                      "quantity": fmt(qty), "price": fmt(ref)}
            what = f"{fmt(qty)} {o.coin} limit {money(ref)}"
        else:
            params = {"symbol": o.symbol, "side": side, "type": "MARKET",
                      "quoteOrderQty": fmt(o.amount), "newOrderRespType": "FULL"}
            what = f"≈ {fmt(rules.round_qty(o.amount / last))} {o.coin} at market ≈ {money(last)}"
        amount_txt = "" if sell_all_qty is not None else f"{money(o.amount)} USDT · "
        summary = f"SPOT {side} {o.symbol} · {amount_txt}{what} · no leverage"
        return Plan(o, text, self._tag(summary), [("spot.new_order", params)])

    def _plan_close(self, o: ClosePosition, text: str) -> Plan:
        if o.coin:
            self._check_coin(o.coin)
        positions = [p for p in self.futures.positions()
                     if o.symbol is None or p["symbol"] == o.symbol]
        # Symbols with open futures orders but no position (e.g. an unfilled limit entry,
        # or a TP/SL left behind): "close" cancels those too, else they can't be removed.
        held = {p["symbol"] for p in positions}
        orphan: dict[str, int] = {}
        for order in self.futures.open_orders(o.symbol):
            if order["symbol"] not in held:
                orphan[order["symbol"]] = orphan.get(order["symbol"], 0) + 1
        if not positions and not orphan:
            raise ValidationError(f"No open {o.coin + ' ' if o.coin else ''}futures position or order.")
        steps, lines = [], []
        for sym, n in orphan.items():
            steps.append(("futures.cancel_all", {"symbol": sym}))
            lines.append(f"{sym} cancel {n} open order{'s' if n > 1 else ''} (no position)")
        for p in positions:
            amt = Decimal(p["positionAmt"])
            side = "SELL" if amt > 0 else "BUY"
            steps.append(("futures.cancel_all", {"symbol": p["symbol"]}))
            steps.append(("futures.new_order", {
                "symbol": p["symbol"], "side": side, "type": "MARKET",
                "quantity": fmt(abs(amt)), "reduceOnly": "true", "newOrderRespType": "RESULT",
            }))
            pnl = Decimal(p.get("unRealizedProfit", "0"))
            lines.append(f"{p['symbol']} {'LONG' if amt > 0 else 'SHORT'} {fmt(abs(amt))} "
                         f"(uPnL {pnl:+,.2f} USDT)")
        head = "CLOSE at market (and cancel its open orders): " if positions else "CANCEL orders: "
        summary = head + "; ".join(lines)
        return Plan(o, text, self._tag(summary), steps)

    def _tag(self, summary: str) -> str:
        return f"{summary} · {self.s.env.upper()}"

    # ---- execution ----------------------------------------------------------

    def _call(self, name: str, params: dict) -> Any:
        target, method = name.split(".")
        client = self.futures if target == "futures" else self.spot
        return getattr(client, method)(**params)

    def _execute(self, plan: Plan) -> Reply:
        results: list[dict] = []
        cmd = plan.command
        try:
            for i, (name, params) in enumerate(plan.steps):
                try:
                    res = self._call(name, params)
                except BinanceError as e:
                    is_protective = params.get("closePosition") == "true"
                    if is_protective and results:
                        # Entry is in; TP/SL failed. Keep going and shout about it.
                        results.append({"step": name, "params": params, "error": str(e)})
                        continue
                    raise
                results.append({"step": name, "params": params, "result": res})
        except BinanceError as e:
            self.log.add(env=self.s.env, text=plan.text, command=cmd.model_dump(mode="json"),
                         summary=plan.summary, status="failed",
                         detail={"results": results, "error": str(e)})
            placed = [r for r in results if r["step"].endswith("new_order")]
            note = " Some orders were already placed, check positions." if placed else " Nothing was placed."
            return Reply("error", f"Failed: {e}.{note}", data=results)

        errors = [r for r in results if "error" in r]
        status = "partial" if errors else "ok"
        self.log.add(env=self.s.env, text=plan.text, command=cmd.model_dump(mode="json"),
                     summary=plan.summary, status=status, detail=results)
        return Reply("done", self._report(plan, results), data=results)

    def _report(self, plan: Plan, results: list[dict]) -> str:
        lines = []
        for r in results:
            if not r["step"].endswith("new_order"):
                continue
            p = r["params"]
            if "error" in r:
                lines.append(f"⚠️ {p['type']} at {p.get('stopPrice')} was NOT placed: {r['error']}. "
                             "Set it manually!")
                continue
            res = r["result"]
            avg = res.get("avgPrice")
            if (not avg or Decimal(avg) == 0) and res.get("fills"):
                qty = sum(Decimal(f["qty"]) for f in res["fills"])
                avg = fmt(sum(Decimal(f["price"]) * Decimal(f["qty"]) for f in res["fills"]) / qty)
            if avg and Decimal(avg) != 0:
                price = f"price {avg}"
            elif _nonzero(res.get("stopPrice")) or _nonzero(p.get("stopPrice")):
                price = f"trigger {_nonzero(res.get('stopPrice')) or p['stopPrice']}"
            else:
                price = f"price {_nonzero(res.get('price')) or p.get('price', '-')}"
            if p.get("closePosition") == "true":
                qty = "whole position"
            else:
                qty = (_nonzero(res.get("executedQty")) or _nonzero(res.get("origQty"))
                       or p.get("quantity") or "-")
            lines.append(f"{res.get('symbol')} {res.get('side')} {res.get('type') or p['type']} "
                         f"{res.get('status', '')} · qty {qty} · {price} · id {res.get('orderId')}")
        if not lines and any(r["step"] == "futures.cancel_all" for r in results):
            return "Open orders cancelled."
        return "\n".join(lines) or "Done."

    # ---- read-only ----------------------------------------------------------

    def prices(self) -> dict[str, str]:
        """Futures mark price for each whitelisted coin (for the order panel)."""
        out = {}
        for coin in self.s.whitelist:
            try:
                out[coin] = str(self.futures.mark_price(f"{coin}USDT"))
            except (BinanceError, KeyError):
                out[coin] = None
        return out

    def _query(self, what: str) -> tuple[str, Any]:
        if what == "positions":
            pos = self.futures.positions()
            if not pos:
                return "No open futures positions.", []
            lines = []
            for p in pos:
                amt = Decimal(p["positionAmt"])
                pnl = Decimal(p["unRealizedProfit"])
                liq = _nonzero(p.get("liquidationPrice"))
                lines.append(
                    f"{p['symbol']} {'LONG' if amt > 0 else 'SHORT'} {fmt(abs(amt))} · "
                    f"entry {money(Decimal(p['entryPrice']))} · mark {money(Decimal(p['markPrice']))} · "
                    f"{p.get('leverage', '?')}x · uPnL {pnl:+,.2f} USDT"
                    + (f" · liq {money(Decimal(liq))}" if liq else ""))
            return "\n".join(lines), pos
        if what == "orders":
            fut = self.futures.open_orders()
            spot = self.spot.open_orders()
            rows = [("futures", o) for o in fut] + [("spot", o) for o in spot]
            if not rows:
                return "No open orders.", []
            lines = []
            for w, o in rows:
                parts = [f"[{w}] {o['symbol']} {o['side']} {o['type']}"]
                if o.get("closePosition") in (True, "true") or not _nonzero(o.get("origQty")):
                    parts.append("whole position")
                else:
                    parts.append(f"qty {fmt(Decimal(o['origQty']))}")
                if _nonzero(o.get("price")):
                    parts.append(f"price {money(Decimal(o['price']))}")
                if _nonzero(o.get("stopPrice")):
                    parts.append(f"trigger {money(Decimal(o['stopPrice']))}")
                lines.append(" ".join(parts) + f" · id {o['orderId']}")
            return "\n".join(lines), {"futures": fut, "spot": spot}
        fut = self.futures.balance("USDT")
        spot = self.spot.balances()
        spot_txt = ", ".join(f"{b['asset']} {fmt(Decimal(b['free']))}" for b in spot) or "empty"
        return (f"Futures: {money(Decimal(fut['balance']))} USDT "
                f"({money(Decimal(fut['available']))} available)\nSpot: {spot_txt}",
                {"futures": fut, "spot": spot})

    def _expire(self) -> None:
        now = self.clock()
        for t in [t for t, p in self._pending.items() if p.expires_at < now]:
            del self._pending[t]


def build_service(settings: Settings) -> TradeService:
    if settings.env == "demo":
        from .exchange.demo import DemoFutures, DemoSpot
        return TradeService(settings, DemoFutures(), DemoSpot(), TradeLog(settings.db_path))
    futures = FuturesClient(settings.futures_url, settings.futures_api_key,
                            settings.futures_api_secret, settings.recv_window_ms)
    spot = SpotClient(settings.spot_url, settings.spot_api_key, settings.spot_api_secret,
                      settings.recv_window_ms)
    return TradeService(settings, futures, spot, TradeLog(settings.db_path))
