"""QUANTOS Data Layer (blueprint §01 / Phase 2).

Point-in-time, survivorship-bias-free data foundation. All time-bearing reads go
through the PIT store / as_of semantics so no downstream consumer can observe
data before it was knowable (CT-1). Pure-Python and deterministic; no real
market-data feed (ingestion is behind an interface).
"""
