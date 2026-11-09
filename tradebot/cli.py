"""Command line: the milestone-2 "place/close/list via a Python script".

    python -m tradebot "long btc 100 5x tp 72k sl 66k"
    python -m tradebot            # interactive prompt
"""
from __future__ import annotations

import sys

from .config import get_settings
from .service import Reply, TradeService, build_service


def handle(svc: TradeService, text: str, ask=input) -> Reply:
    reply = svc.preview(text)
    print(reply.message)
    if reply.status != "confirm":
        return reply
    answer = ask(f"Confirm within {svc.s.confirm_ttl_seconds}s? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        print(svc.cancel(reply.token).message)
        return reply
    result = svc.confirm(reply.token)
    print(result.message)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Summaries use ≈ · ↔ ⚠️; a Windows console or redirected output defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    svc = build_service(settings)
    print(f"[tradebot · {settings.env.upper()} · whitelist {', '.join(settings.whitelist)}]")
    if argv:
        reply = handle(svc, " ".join(argv))
        return 0 if reply.status in {"done", "info", "confirm"} else 1
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text in {"q", "quit", "exit"}:
            return 0
        if text:
            handle(svc, text)


if __name__ == "__main__":
    raise SystemExit(main())
