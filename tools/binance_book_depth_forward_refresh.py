"""Daily L2 wrapper for checksum-verified Binance book-depth archives.

All mutable outputs are rooted under ``QUANTOS_STATE_ROOT`` by the immutable
schedule launcher.  The delegated collector uses only Binance Data Vision's
public, keyless archive endpoint and has no account, credential, broker, venue
session, Testnet, Mainnet, or order capability.
"""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.binance_book_depth_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    return int(
        fetch_main(
            [
                "--store",
                str(cache_root / "binance_book_depth_archive.db"),
                "--archive-root",
                str(cache_root / "raw_market_data"),
                "--status",
                str(cache_root / "binance_book_depth_archive_status.json"),
                "--days",
                "1",
                "--workers",
                "4",
                "--availability-lookback-days",
                "3",
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
