"""Daily L2 incremental wrapper for public Hyperliquid market history."""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.hyperliquid_public_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    fetch_main([
        "--days", "7",
        "--out", str(cache_root / "hyperliquid_public_market.json"),
        "--archive-root", str(cache_root / "raw_market_data"),
        "--max-pages", "5",
        "--merge-existing",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

