"""Rule parser for the standard command shapes in the spec.

Examples:
    long btc 100 5x
    short eth 50 10x tp 2900 sl 3400
    long sol 30 3x at 140
    buy btc 50            (spot, no leverage)
    close btc / close all
    positions / orders / balance

Anything that doesn't match raises ParseError; the web app will hand that text
to Ollama later (milestone 6), which must produce the same structured command.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import ClosePosition, Command, FuturesOrder, Incomplete, Query, SpotOrder


class ParseError(ValueError):
    pass


QUERY_WORDS = {
    "positions": "positions", "position": "positions", "pos": "positions",
    "orders": "orders", "order": "orders",
    "balance": "balance", "balances": "balance", "bal": "balance",
}
TRADE_VERBS = {"long", "short", "buy", "sell"}
NOT_YET = {
    "deposit": "Deposit only works on live Binance, so it comes with milestone 7.",
    "transfer": "Spot ↔ futures transfer only works on live Binance, so it comes with milestone 7.",
    "withdraw": "Withdrawals are phase 2 and are not built.",
}
UNIT_WORDS = {"usdt", "usd", "$"}
FILLER = {"market", "with", "of", "worth", "margin", "lev", "leverage", "please", "open"}
OTHER_CURRENCIES = {"chf", "eur", "usdc", "busd", "gbp"}

_NUM = r"\d+(?:\.\d+)?k?"
_LEV = re.compile(r"^(?:(\d+)x|x(\d+))$")


def _num(tok: str) -> Decimal:
    tok = tok.lower()
    mult = Decimal(1)
    if tok.endswith("k"):
        mult, tok = Decimal(1000), tok[:-1]
    try:
        value = Decimal(tok) * mult
    except InvalidOperation as e:
        raise ParseError(f"'{tok}' is not a number") from e
    if value <= 0:
        raise ParseError("Numbers must be positive")
    return value


def _is_num(tok: str) -> bool:
    return re.fullmatch(_NUM, tok) is not None


def _normalise(text: str) -> list[str]:
    t = text.strip().lower()
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)          # 72,000 -> 72000
    t = t.replace("@", " at ")
    t = re.sub(r"\$\s*(\d)", r"\1", t)                   # $100 -> 100
    t = re.sub(r"(\d)(usdt|usd)\b", r"\1 \2", t)         # 100usdt -> 100 usdt
    t = re.sub(r"\b(tp|sl)(\d)", r"\1 \2", t)            # tp72k -> tp 72k
    t = re.sub(r"(\d)\s+x\b", r"\1x", t)                 # 5 x -> 5x
    return t.split()


def _coin(tok: str) -> str:
    c = tok.upper()
    if c.endswith("USDT") and len(c) > 4:
        c = c[:-4]
    if not re.fullmatch(r"[A-Z0-9]{2,12}", c) or c.isdigit():
        raise ParseError(f"'{tok}' doesn't look like a coin")
    return c


def parse(text: str) -> Command | Incomplete:
    tokens = _normalise(text)
    if not tokens:
        raise ParseError("Empty command")

    verb = tokens[0]
    rest = tokens[1:]

    if verb in NOT_YET:
        raise ParseError(NOT_YET[verb])

    if verb in QUERY_WORDS and not rest:
        return Query(what=QUERY_WORDS[verb])
    if verb in {"open", "show", "list"} and len(rest) == 1 and rest[0] in QUERY_WORDS:
        return Query(what=QUERY_WORDS[rest[0]])

    if verb == "close":
        if len(rest) != 1:
            raise ParseError("Say 'close <coin>' or 'close all'")
        if rest[0] == "all":
            return ClosePosition(coin=None)
        return ClosePosition(coin=_coin(rest[0]))

    if verb not in TRADE_VERBS:
        raise ParseError(f"Unknown command '{verb}'")

    coin = amount = price = tp = sl = None
    leverage: int | None = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        if tok in {"tp", "sl", "at"}:
            if nxt is None or not _is_num(nxt):
                raise ParseError(f"'{tok}' needs a price after it")
            value = _num(nxt)
            if tok == "tp":
                if tp is not None:
                    raise ParseError("TP given twice")
                tp = value
            elif tok == "sl":
                if sl is not None:
                    raise ParseError("SL given twice")
                sl = value
            else:
                if price is not None:
                    raise ParseError("Limit price given twice")
                price = value
            i += 2
            continue
        if tok == "limit":
            # "limit 3200" is accepted as "at 3200"; bare "limit" just waits for "at".
            if nxt is not None and _is_num(nxt) and price is None and amount is not None:
                price = _num(nxt)
                i += 2
                continue
            i += 1
            continue
        m = _LEV.match(tok)
        if m:
            if leverage is not None:
                raise ParseError("Leverage given twice")
            leverage = int(m.group(1) or m.group(2))
            i += 1
            continue
        if _is_num(tok):
            if amount is not None:
                raise ParseError(f"Two amounts ({amount} and {tok}); use 'at', 'tp' or 'sl' for prices")
            amount = _num(tok)
            i += 1
            continue
        if tok in UNIT_WORDS or tok in FILLER:
            i += 1
            continue
        if tok in OTHER_CURRENCIES:
            raise ParseError("Amounts are USDT only")
        if coin is not None:
            raise ParseError(f"Two coins ({coin} and {tok.upper()})")
        coin = _coin(tok)
        i += 1

    partial = {"side": verb, "coin": coin, "amount": amount, "leverage": leverage,
               "price": price, "tp": tp, "sl": sl}
    missing = []
    if coin is None:
        missing.append("coin")
    if amount is None:
        missing.append("amount")

    if verb in {"buy", "sell"}:
        if leverage is not None:
            raise ParseError("Spot buy/sell has no leverage; use 'long'/'short' for futures")
        if tp is not None or sl is not None:
            raise ParseError("TP/SL are for futures (long/short) only for now")
        if missing:
            return _incomplete(missing, partial)
        return SpotOrder(side=verb, coin=coin, amount=amount, price=price)

    if leverage is None:
        missing.append("leverage")
    if missing:
        return _incomplete(missing, partial)
    return FuturesOrder(side=verb, coin=coin, amount=amount, leverage=leverage,
                        price=price, tp=tp, sl=sl)


_QUESTIONS = {
    "coin": "Which coin?",
    "amount": "How many USDT?",
    "leverage": "What leverage (e.g. 5x)?",
}


def _incomplete(missing: list[str], partial: dict) -> Incomplete:
    question = " ".join(_QUESTIONS[m] for m in missing)
    return Incomplete(missing=missing, question=question,
                      partial={k: (str(v) if v is not None else None) for k, v in partial.items()})
