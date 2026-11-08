"""HTTP API that the web app (milestone 4) and the MCP server (milestone 5) sit on.

No login yet: it binds to 127.0.0.1 only. Don't expose it until milestone 4
adds the password + 2FA, and keep it behind Tailscale after that.

    uvicorn tradebot.api:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from .config import get_settings
from .service import TradeService, build_service

app = FastAPI(title="tradebot", version="0.1.0")


@lru_cache
def get_service() -> TradeService:
    return build_service(get_settings())


class CommandIn(BaseModel):
    text: str


class TokenIn(BaseModel):
    token: str


@app.get("/health")
def health(svc: TradeService = Depends(get_service)) -> dict:
    return {"ok": True, "env": svc.s.env, "whitelist": svc.s.whitelist}


@app.post("/api/preview")
def preview(body: CommandIn, svc: TradeService = Depends(get_service)) -> dict:
    """Parse + validate + summary. Returns a token when the order needs confirming."""
    return svc.preview(body.text).to_dict()


@app.post("/api/confirm")
def confirm(body: TokenIn, svc: TradeService = Depends(get_service)) -> dict:
    return svc.confirm(body.token).to_dict()


@app.post("/api/cancel")
def cancel(body: TokenIn, svc: TradeService = Depends(get_service)) -> dict:
    return svc.cancel(body.token).to_dict()


@app.get("/api/positions")
def positions(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("positions").to_dict()


@app.get("/api/orders")
def orders(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("orders").to_dict()


@app.get("/api/balance")
def balance(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("balance").to_dict()


@app.get("/api/log")
def log(limit: int = 50, svc: TradeService = Depends(get_service)) -> list[dict]:
    return svc.log.recent(limit)
