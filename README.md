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
| 4. Web app behind Tailscale + login with 2FA | next |
| 5. MCP server for Claude | — |
| 6. Ollama free-text parsing | — |
| 7. Live with small sizes; deposit + spot↔futures transfer | — |

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # then paste your testnet keys
```

Spot and futures testnets are **separate sites with separate keys**:

- Spot: <https://testnet.binance.vision> → log in with GitHub → Generate HMAC_SHA256 key
- Futures: <https://testnet.binancefuture.com> → log in → "API Key" tab under the order panel

Neither testnet supports deposits or wallet transfers, so those wait for milestone 7.

## Use

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

HTTP API (base for the web app and MCP server; **no login yet, so localhost only**):

```bash
uvicorn tradebot.api:app --host 127.0.0.1 --port 8000
# POST /api/preview {"text": "..."} → {status, message, token}
# POST /api/confirm {"token": "..."} · POST /api/cancel · GET /api/positions|orders|balance|log
```

### Commands

| Text | Meaning |
|---|---|
| `long btc 100 5x` | futures market long, 100 USDT margin, 5x (position ≈ 500) |
| `short eth 50 10x tp 2900 sl 3400` | short with TP/SL attached (mark-price triggered, close whole position) |
| `long sol 30 3x at 140` (or `@140`) | limit order |
| `buy btc 50` / `sell btc 50` | spot market buy/sell of 50 USDT worth, no leverage |
| `buy btc 50 at 60000` | spot limit |
| `close btc` / `close all` | cancel that symbol's open orders, then reduce-only market close |
| `positions` / `orders` / `balance` | read-only, no confirmation |

Amounts are USDT only (`100`, `100 usdt`, `$100` are the same). Prices accept `72k` and `72,000`.
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
