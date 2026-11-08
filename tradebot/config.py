"""Settings, read from environment variables (prefix TRADEBOT_) or a .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_URLS = {
    "demo": {"spot": "", "futures": ""},
    "testnet": {
        "spot": "https://testnet.binance.vision",
        "futures": "https://testnet.binancefuture.com",
    },
    "live": {
        "spot": "https://api.binance.com",
        "futures": "https://fapi.binance.com",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRADEBOT_", env_file=".env", extra="ignore")

    # "demo" = fake in-memory exchange, no keys needed, nothing sent anywhere.
    env: Literal["demo", "testnet", "live"] = "testnet"

    spot_api_key: str = ""
    spot_api_secret: str = ""
    futures_api_key: str = ""
    futures_api_secret: str = ""

    # Override only if Binance moves the testnet hosts.
    spot_base_url: str | None = None
    futures_base_url: str | None = None

    # Hard safety cap: only these base coins can be traded (quote is always USDT).
    whitelist: Annotated[list[str], NoDecode] = ["BTC", "ETH", "SOL"]
    max_leverage: int = 10
    margin_type: Literal["ISOLATED", "CROSSED"] = "ISOLATED"
    confirm_ttl_seconds: int = 60
    # Rough maintenance-margin rate used only for the liquidation estimate in the summary.
    maint_margin_rate: float = 0.004

    db_path: str = "tradebot.db"

    # Web login (milestone 4). Generate both with: python -m tradebot.auth
    web_password_hash: str = ""
    web_totp_secret: str = ""
    web_session_hours: float = 12
    # Set true when served over HTTPS (e.g. `tailscale serve`), so the cookie is HTTPS-only.
    web_cookie_secure: bool = False
    recv_window_ms: int = 5000

    @field_validator("whitelist", mode="before")
    @classmethod
    def _split_whitelist(cls, v):
        if isinstance(v, str):
            v = v.split(",")
        return [c.strip().upper() for c in v if c and c.strip()]

    @property
    def spot_url(self) -> str:
        return self.spot_base_url or BASE_URLS[self.env]["spot"]

    @property
    def futures_url(self) -> str:
        return self.futures_base_url or BASE_URLS[self.env]["futures"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
