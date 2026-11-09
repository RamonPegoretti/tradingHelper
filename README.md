# tradingHelper

Binance trade bot: place orders by typing them in plain text
(`long btc 100 5x tp 72k sl 66k`). The bot shows a summary, you confirm, then it
places the order. Spec: "Binance Trade Bot — MVP Spec" (Claude Doc).

> A tool for your own decisions, not trading advice. Leverage multiplies losses as much as gains.

## Status

| Milestone | State |
|---|---|
| 1. Oracle server up and locked down | not in this repo (server setup) |
| 2. Testnet core: place / close / list via a Python script | ✅ built, not yet run against testnet |
| 3. Rule parser + validation + confirmation flow | ✅ built |
| 4. Web app behind Tailscale + login with 2FA | ✅ built |
| 5. MCP server for Claude | next |
| 6. Ollama free-text parsing | — |
| 7. Live with small sizes; deposit + spot↔futures transfer | — |

## Try it in 5 minutes (demo mode, no Binance keys)

Demo mode uses a fake in-memory exchange (10,000 USDT futures, 5,000 USDT spot). Nothing is sent anywhere.

```bash
git clone https://github.com/RamonPegoretti/tradingHelper && cd tradingHelper
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
python -m tradebot.auth          # pick a password, add the code to your authenticator app,
                                 # paste the two printed lines into .env
```

In `.env` set `TRADEBOT_ENV=demo`, then:

```bash
uvicorn tradebot.api:app --host 127.0.0.1 --port 8000
```

### On Windows (PowerShell)

```powershell
git clone https://github.com/RamonPegoretti/tradingHelper; cd tradingHelper
py -3.12 -m venv .venv                 # Python 3.11+; plain `python` works too if it's 3.11+
.\.venv\Scripts\Activate.ps1           # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"
Copy-Item .env.example .env            # cmd.exe: copy .env.example .env
python -m tradebot.auth                # run it in PowerShell or cmd, not Git Bash (the password prompt hangs there)
notepad .env                           # paste the two lines, set TRADEBOT_ENV=demo, save
uvicorn tradebot.api:app --host 127.0.0.1 --port 8000
```

- Use `127.0.0.1`, not `0.0.0.0`: the bot should only be reachable from your own machine.
- Notepad's UTF-8 BOM is fine; the app reads `.env` either way.
- Don't put the password hash in a PowerShell `$env:` variable with double quotes: PowerShell
  eats the `$` parts. Keep it in `.env`.
- Run `uvicorn` from the repo folder; that's where it looks for `.env` and writes `tradebot.db`.

Open <http://127.0.0.1:8000>, log in with the password + the 6-digit code.

## Testnet

Set `TRADEBOT_ENV=testnet` and paste your testnet keys into `.env`.
Spot and futures testnets are **separate sites with separate keys**:

- Spot: <https://testnet.binance.vision> → log in with GitHub → Generate HMAC_SHA256 key
- Futures: <https://testnet.binancefuture.com> → log in → "API Key" tab under the order panel

Neither testnet supports deposits or wallet transfers, so those wait for milestone 7.

## On the Oracle server, behind Tailscale

The app listens on 127.0.0.1 only; Tailscale publishes it over HTTPS to your tailnet
(not the internet):

```bash
sudo cp deploy/tradebot.service /etc/systemd/system/   # edit User/paths first
sudo systemctl enable --now tradebot
sudo tailscale serve --bg 8000                        # https://<server-name>.<tailnet>.ts.net
```

With HTTPS via `tailscale serve`, set `TRADEBOT_WEB_COOKIE_SECURE=true`.
Never use `tailscale funnel` for this; that would put it on the public internet.

## Use

### Web app

Pick Futures or Spot, tap a coin, an amount and a leverage, then **Long** or **Short**
(Buy/Sell on spot). Limit price, TP and SL are optional under "Limit price / TP / SL".
A summary appears under the buttons: tap **Place order** within 60 s, or Cancel.
Each open position has its own **Close** button. Prices, positions and balance refresh every 10 s.
Typing still works in the small box at the bottom (Enter previews, Enter again places, Esc cancels).

### Command line

```bash
python -m tradebot "long btc 100 5x tp 72k sl 66k"   # one command
python -m tradebot                                   # interactive prompt
```

```
> long btc 100 5x tp 72k sl 66k
LONG BTCUSDT · margin 100 USDT · 5x · position ≈ 470.75 USDT (0.007 BTC) · entry ≈ market 67,250.10
 · TP 72,000 · SL 66,000 · est. liquidation ≈ 54,016.15 · isolated · TESTNET
Confirm within 60s? [y/N]
```

### HTTP API (used by the web app; all behind login)

`POST /api/preview {"text"}` → `{status, message, token}` · `POST /api/confirm {"token"}` ·
`POST /api/cancel` · `GET /api/positions|orders|balance|log`. POSTs need the header `X-Tradebot: 1`.

### Commands

| Text | Meaning |
|---|---|
| `long btc 100 5x` | futures market long, 100 USDT margin, 5x (position ≈ 500) |
| `short eth 50 10x tp 2900 sl 3400` | short with TP/SL attached (mark-price triggered, close whole position) |
| `long sol 30 3x at 140` (or `@140`) | limit order |
| `buy btc 50` / `sell btc 50` | spot market buy/sell of 50 USDT worth, no leverage |
| `buy btc 50 at 60000` | spot limit |
| `close btc` / `close all` | cancel that symbol's open orders, then reduce-only market close (with no position, just cancels its open futures orders, e.g. an unfilled limit) |
| `positions` / `orders` / `balance` | read-only, no confirmation |

Amounts are USDT only (`100`, `100 usdt`, `$100` are the same). Prices accept `72k`, `72,000` and `72'000`;
`12,5` is read as 12.5. `sell btc 50` sells your whole BTC if it's worth a little under 50 (≥ 97%).
Missing leverage, amount or coin → the bot asks instead of guessing.

## Safety built in

- **Confirm can't be skipped.** Order endpoints are only reachable through `confirm(token)`;
  tokens are single-use and expire after 60 s so a stale price can't go through.
- **Coin whitelist** (default BTC, ETH, SOL) and **max leverage** (default 10x).
- **Isolated margin** by default, set before every futures order.
- Checks before the summary: symbol exists, Binance minimum quantity/notional, enough balance,
  TP/SL on the right side of the entry.
- If the entry fills but a TP/SL is rejected, the reply says so loudly ("Set it manually!").
- Every confirmed action and Binance's responses go to the SQLite log (`tradebot.db`).
- Use a Binance key with **trading only, withdrawals off, restricted to the server's IP**.
- Web login: password (scrypt) + TOTP 2FA, a code can't be reused, 5 failures lock login for 5 min,
  HttpOnly SameSite=Strict session cookie, CSRF header on every POST, strict CSP, no API docs exposed.

## Defaults chosen for open questions (all in `.env`)

| Open question | Default for now |
|---|---|
| Whitelist coins | `BTC,ETH,SOL` |
| Isolated margin / leverage cap | `ISOLATED`, max `10x` |
| Futures position mode | assumes **one-way** mode (Binance default); hedge mode isn't supported |
| Testnet hosts | `testnet.binance.vision`, `testnet.binancefuture.com` (override with `TRADEBOT_SPOT_BASE_URL` / `TRADEBOT_FUTURES_BASE_URL`) |

## Tests

```bash
pytest
```

The exchange is faked in tests; nothing has been run against a real testnet yet.
Sessions live in memory, so restarting the app logs you out.
