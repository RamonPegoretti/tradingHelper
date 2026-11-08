"""Structured commands produced by the parser (rule parser now, Ollama later)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Union

from pydantic import BaseModel


class FuturesOrder(BaseModel):
    kind: Literal["futures"] = "futures"
    side: Literal["long", "short"]
    coin: str
    amount: Decimal          # margin in USDT
    leverage: int
    price: Decimal | None = None   # None = market, else limit price
    tp: Decimal | None = None
    sl: Decimal | None = None

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"


class SpotOrder(BaseModel):
    kind: Literal["spot"] = "spot"
    side: Literal["buy", "sell"]
    coin: str
    amount: Decimal          # USDT value to buy or sell
    price: Decimal | None = None

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"


class ClosePosition(BaseModel):
    kind: Literal["close"] = "close"
    coin: str | None = None  # None = close all

    @property
    def symbol(self) -> str | None:
        return f"{self.coin}USDT" if self.coin else None


class Query(BaseModel):
    kind: Literal["query"] = "query"
    what: Literal["positions", "orders", "balance"]


Command = Union[FuturesOrder, SpotOrder, ClosePosition, Query]


class Incomplete(BaseModel):
    """The text was understood but a required piece is missing: ask, don't guess."""
    missing: list[str]
    question: str
    partial: dict = {}
