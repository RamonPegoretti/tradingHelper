"""Web app + HTTP API (milestone 4). The MCP server (milestone 5) will sit on the same service.

Login is password + TOTP 2FA. Keep it reachable only through Tailscale:
bind to 127.0.0.1 and publish with `tailscale serve` (see README).

    uvicorn tradebot.api:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from .auth import AuthManager
from .config import get_settings
from .service import TradeService, build_service

WEB = Path(__file__).parent / "web"
STATIC = {"app.js": "text/javascript", "login.js": "text/javascript", "style.css": "text/css"}
COOKIE = "tradebot_session"
CSRF_HEADER = "x-tradebot"

app = FastAPI(title="tradebot", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)


@lru_cache
def get_service() -> TradeService:
    return build_service(get_settings())


@lru_cache
def get_auth() -> AuthManager:
    s = get_settings()
    return AuthManager(s.web_password_hash, s.web_totp_secret, s.web_session_hours)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def require_login(request: Request, auth: AuthManager = Depends(get_auth)) -> None:
    if not auth.check(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Not logged in")
    # Cross-site pages can't set custom headers without a CORS preflight we never allow.
    if request.method != "GET" and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Missing CSRF header")


class CommandIn(BaseModel):
    text: str


class TokenIn(BaseModel):
    token: str


class LoginIn(BaseModel):
    password: str
    code: str


# ---- pages ------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index(request: Request, auth: AuthManager = Depends(get_auth)):
    if not auth.check(request.cookies.get(COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(WEB / "login.html", media_type="text/html")


@app.get("/static/{name}", include_in_schema=False)
def static(name: str):
    if name not in STATIC:
        raise HTTPException(404)
    return FileResponse(WEB / name, media_type=STATIC[name])


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ---- auth -------------------------------------------------------------------

@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response,
          auth: AuthManager = Depends(get_auth)) -> dict:
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Missing CSRF header")
    if not auth.configured:
        raise HTTPException(503, "Web login isn't set up: run `python -m tradebot.auth`")
    if auth.locked():
        raise HTTPException(429, "Too many failed attempts, try again in a few minutes")
    token = auth.login(body.password, body.code)
    if token is None:
        raise HTTPException(401, "Wrong password or code")
    s = get_settings()
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                        secure=s.web_cookie_secure, max_age=int(s.web_session_hours * 3600))
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response, auth: AuthManager = Depends(get_auth)) -> dict:
    auth.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---- trading (all behind login) ----------------------------------------------

@app.get("/api/info", dependencies=[Depends(require_login)])
def info(svc: TradeService = Depends(get_service)) -> dict:
    return {"env": svc.s.env, "whitelist": svc.s.whitelist, "max_leverage": svc.s.max_leverage,
            "margin_type": svc.s.margin_type, "confirm_ttl": svc.s.confirm_ttl_seconds}


@app.post("/api/preview", dependencies=[Depends(require_login)])
def preview(body: CommandIn, svc: TradeService = Depends(get_service)) -> dict:
    """Parse + validate + summary. Returns a token when the order needs confirming."""
    return svc.preview(body.text).to_dict()


@app.post("/api/confirm", dependencies=[Depends(require_login)])
def confirm(body: TokenIn, svc: TradeService = Depends(get_service)) -> dict:
    return svc.confirm(body.token).to_dict()


@app.post("/api/cancel", dependencies=[Depends(require_login)])
def cancel(body: TokenIn, svc: TradeService = Depends(get_service)) -> dict:
    return svc.cancel(body.token).to_dict()


@app.get("/api/positions", dependencies=[Depends(require_login)])
def positions(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("positions").to_dict()


@app.get("/api/orders", dependencies=[Depends(require_login)])
def orders(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("orders").to_dict()


@app.get("/api/balance", dependencies=[Depends(require_login)])
def balance(svc: TradeService = Depends(get_service)) -> dict:
    return svc.preview("balance").to_dict()


@app.get("/api/prices", dependencies=[Depends(require_login)])
def prices(svc: TradeService = Depends(get_service)) -> dict:
    return svc.prices()


@app.get("/api/log", dependencies=[Depends(require_login)])
def log(limit: int = 50, svc: TradeService = Depends(get_service)) -> list[dict]:
    return svc.log.recent(max(1, min(limit, 500)))
