"""Weekly L2 wrapper for official public CFTC TFF crypto history."""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.cftc_tff_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    fetch_main([
        "--out", str(cache_root / "cftc_tff_crypto.json"),
        "--archive-root", str(cache_root / "raw_market_data"),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

