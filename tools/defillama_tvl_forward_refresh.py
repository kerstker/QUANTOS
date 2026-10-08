"""Daily L2 wrapper for public DefiLlama TVL forward-vintage collection.

All mutable outputs are rooted under ``QUANTOS_STATE_ROOT`` by the immutable
schedule launcher.  The delegated fetcher is keyless and read-only; this worker
has no account, broker, credential, Testnet, or order capability.
"""
from __future__ import annotations

from quantos.runtime_paths import runtime_state_root
from tools.defillama_chain_tvl_fetch import main as fetch_main


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    fetch_main(
        [
            "--out",
            str(cache_root / "defillama_chain_tvl.json"),
            "--archive-root",
            str(cache_root / "raw_market_data"),
            "--vintage-db",
            str(cache_root / "defillama_tvl_vintages.db"),
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
