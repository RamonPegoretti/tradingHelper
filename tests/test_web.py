import pytest
from fastapi.testclient import TestClient

import tradebot.api as api
from tradebot.auth import AuthManager, hash_password, new_totp_secret, otpauth_uri, totp, verify_totp
from tradebot.config import Settings
from tradebot.service import build_service

H = {"X-Tradebot": "1"}


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path):
    secret = new_totp_secret()
    clock = Clock()
    auth = AuthManager(hash_password("correct horse battery"), secret, clock=clock)
    settings = Settings(_env_file=None, env="demo", db_path=str(tmp_path / "t.db"))
    svc = build_service(settings)
    api.app.dependency_overrides[api.get_auth] = lambda: auth
    api.app.dependency_overrides[api.get_service] = lambda: svc
    client = TestClient(api.app)
    yield client, auth, secret, clock
    api.app.dependency_overrides.clear()


def login(client, secret, clock, pw="correct horse battery"):
    return client.post("/api/login", json={"password": pw, "code": totp(secret, clock())}, headers=H)


def test_totp_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890", T=59 -> 94287082 (8 digits)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, 59, digits=8) == "94287082"
    assert verify_totp(secret, totp(secret, 1000), 1000 + 30)
    assert not verify_totp(secret, totp(secret, 1000), 1000 + 120)
    assert otpauth_uri("ABC").startswith("otpauth://totp/")


def test_pages_and_api_require_login(ctx):
    client, *_ = ctx
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.post("/api/preview", json={"text": "balance"}, headers=H).status_code == 401
    assert client.get("/api/positions").status_code == 401
    assert "frame-ancestors 'none'" in client.get("/login").headers["content-security-policy"]


def test_login_flow_and_trade(ctx):
    client, auth, secret, clock = ctx
    assert login(client, secret, clock).status_code == 200
    assert client.get("/").status_code == 200
    r = client.post("/api/preview", json={"text": "long btc 100 5x tp 80k sl 60k"}, headers=H).json()
    assert r["status"] == "confirm" and "DEMO" in r["message"]
    done = client.post("/api/confirm", json={"token": r["token"]}, headers=H).json()
    assert done["status"] == "done"
    pos = client.get("/api/positions").json()
    assert "BTCUSDT LONG" in pos["message"]
    assert "TAKE_PROFIT_MARKET" in client.get("/api/orders").json()["message"]
    client.post("/api/logout", headers=H)
    assert client.get("/api/positions").status_code == 401


def test_csrf_header_required(ctx):
    client, auth, secret, clock = ctx
    assert client.post("/api/login", json={"password": "x", "code": "1"}).status_code == 403
    login(client, secret, clock)
    assert client.post("/api/preview", json={"text": "balance"}).status_code == 403


def test_wrong_code_and_lockout(ctx):
    client, auth, secret, clock = ctx
    for _ in range(5):
        assert client.post("/api/login", json={"password": "correct horse battery", "code": "000000"},
                           headers=H).status_code == 401
    assert login(client, secret, clock).status_code == 429
    clock.t += 301
    assert login(client, secret, clock).status_code == 200


def test_code_cannot_be_replayed(ctx):
    client, auth, secret, clock = ctx
    assert login(client, secret, clock).status_code == 200
    assert login(TestClient(api.app), secret, clock).status_code == 401


def test_wrong_password(ctx):
    client, auth, secret, clock = ctx
    assert login(client, secret, clock, pw="nope").status_code == 401


def test_static_allowlist(ctx):
    client, *_ = ctx
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/..%2Fapi.py").status_code == 404


def test_demo_spot_and_close(ctx):
    client, auth, secret, clock = ctx
    login(client, secret, clock)
    for text in ["buy btc 50", "short eth 100 3x", "close all"]:
        r = client.post("/api/preview", json={"text": text}, headers=H).json()
        assert r["status"] == "confirm", r
        assert client.post("/api/confirm", json={"token": r["token"]}, headers=H).json()["status"] == "done"
    assert client.get("/api/positions").json()["message"] == "No open futures positions."
    assert "BTC" in client.get("/api/balance").json()["message"]


def test_prices_for_panel(ctx):
    client, auth, secret, clock = ctx
    login(client, secret, clock)
    prices = client.get("/api/prices").json()
    assert set(prices) == {"BTC", "ETH", "SOL"} and float(prices["BTC"]) > 0


def test_query_data_is_returned_as_data(ctx):
    client, auth, secret, clock = ctx
    login(client, secret, clock)
    bal = client.get("/api/balance").json()
    assert "token" not in bal and bal["data"]["futures"]["asset"] == "USDT"
