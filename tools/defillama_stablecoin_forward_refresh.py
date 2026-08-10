"""Daily L2 wrapper for public DefiLlama stablecoin forward vintages."""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.defillama_stablecoin_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    fetch_main([
        "--out", str(cache_root / "defillama_stablecoin_supply.json"),
        "--archive-root", str(cache_root / "raw_market_data"),
        "--vintage-db", str(cache_root / "defillama_stablecoin_vintages.db"),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

