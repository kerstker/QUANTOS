"""Daily L2 wrapper for public OKX borrow-rate forward vintages."""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.okx_borrow_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    fetch_main(
        [
            "--out",
            str(cache_root / "okx_public_borrow_rates.json"),
            "--archive-root",
            str(cache_root / "raw_market_data"),
            "--vintage-db",
            str(cache_root / "okx_borrow_vintages.db"),
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
