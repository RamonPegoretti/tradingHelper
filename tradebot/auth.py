"""Single-user login: password (scrypt hash) + TOTP 2FA, in-memory sessions.

Generate the values for .env with:  python -m tradebot.auth
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

# ---- password ---------------------------------------------------------------

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(dk.hex(), dk_hex)


# ---- TOTP (RFC 6238, works with any authenticator app) ----------------------

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, at: float, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(at // step))
    h = hmac.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, at: float, window: int = 1) -> bool:
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    return any(hmac.compare_digest(totp(secret, at + i * 30), code)
               for i in range(-window, window + 1))


def otpauth_uri(secret: str, account: str = "tradebot") -> str:
    return f"otpauth://totp/{quote(account)}?secret={secret}&issuer=tradebot"


# ---- sessions + lockout -----------------------------------------------------

@dataclass
class AuthManager:
    password_hash: str
    totp_secret: str
    session_hours: float = 12
    max_failures: int = 5
    lockout_seconds: int = 300
    clock: Callable[[], float] = time.time
    _sessions: dict[str, float] = field(default_factory=dict)
    _failures: list[float] = field(default_factory=list)
    _used_codes: dict[str, float] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.password_hash and self.totp_secret)

    def locked(self) -> bool:
        now = self.clock()
        self._failures = [t for t in self._failures if now - t < self.lockout_seconds]
        return len(self._failures) >= self.max_failures

    def login(self, password: str, code: str) -> str | None:
        """Returns a session token, or None. Locks out after repeated failures."""
        if not self.configured or self.locked():
            return None
        now = self.clock()
        self._used_codes = {c: t for c, t in self._used_codes.items() if now - t < 90}
        ok = (verify_password(password, self.password_hash)
              and code not in self._used_codes          # no replay of a seen code
              and verify_totp(self.totp_secret, code, now))
        if not ok:
            self._failures.append(now)
            return None
        self._used_codes[code] = now
        self._failures.clear()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + self.session_hours * 3600
        return token

    def check(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self._sessions.get(token)
        if exp is None:
            return False
        if self.clock() > exp:
            del self._sessions[token]
            return False
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


def main() -> None:
    print("Sets up the web login. Paste the two lines it prints into .env.\n")
    pw = getpass.getpass("New web password: ")
    if len(pw) < 12:
        raise SystemExit("Use at least 12 characters.")
    if pw != getpass.getpass("Again: "):
        raise SystemExit("Passwords don't match.")
    secret = new_totp_secret()
    print(f"\nTRADEBOT_WEB_PASSWORD_HASH={hash_password(pw)}")
    print(f"TRADEBOT_WEB_TOTP_SECRET={secret}")
    print("\nAdd this to your authenticator app (Google Authenticator, 1Password, …):")
    print(f"  secret: {secret}")
    print(f"  or URI: {otpauth_uri(secret)}")


if __name__ == "__main__":
    main()
