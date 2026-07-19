# Versioned Multi-Asset Research Evidence

**Status:** approved implementation specification  
**Readiness boundary:** `L2_DRY_RUN_ONLY`  
**Requirement:** `ALPHA.MULTI_ASSET.RESEARCH`

## Problem

The top-tier assessment currently has one `FAILED` requirement.  The repository
contains real non-crypto research code and a local equity cache, but the prior
assessment passed only when an untracked workstation cache happened to exist.
A clean checkout cannot reproduce that result, so it is not institutional
evidence.

## Decision

Retain and replay a non-crypto FX factor baseline from the already-versioned
Frankfurter/ECB PIT bundle:

- `evidence/datasets/fx_pit_tradable_v1.jsonl.gz`
- `evidence/datasets/fx_pit_tradable_v1.manifest.json`

The bundle has 18 tradable currencies and keeps `event_time`, `knowledge_time`,
the canonical bar payload, source query, exact byte digest, and time range.  It
is independent of `data/cache/fx.db` at replay time.

The research-side replay uses the existing IC panel primitives and adds the FX
universe to the declared registry.  It evaluates two deliberately conventional
factors, `fx_momentum_120` and `fx_low_vol_60`, at non-overlapping 1, 5, and 20
period horizons.  The output is diagnostic evidence only; it does not promote
an alpha or claim a tradable edge.

The governance checker independently rebuilds the FX panel and recomputes the
same statistics with Python standard-library code.  It also verifies:

1. the exact gzip bundle and manifest digests;
2. PIT availability (`knowledge_time <= event_time`) and payload identity;
3. fixed universe, factor definitions, horizons, limitations, and L2 boundary;
4. canonical hashes of every implementation file;
5. the evidence self-digest and exact independently replayed factor results.

This avoids a governance-to-research service dependency and prevents a shared
implementation bug from trivially self-certifying.

## Evidence contract

`evidence/multi_asset_research_baseline.json` must contain:

- schema and evidence identifiers;
- primary crypto plus retained non-crypto FX scope;
- source, PIT semantics, row/key/time coverage, and immutable bundle hashes;
- the two factor definitions and replayed multi-horizon IC statistics;
- code hashes, deterministic replay command, limitations, and safety statement;
- a canonical self-digest.

The top-tier requirement uses a semantic
`multi_asset_research_evidence` checker at M3.  A missing, stale, malformed,
future-known, code-drifted, or result-tampered artifact fails closed.

## Non-goals

- No broker, exchange, credential, order, deployment, or live-trading path.
- No claim that the FX factors are profitable or deployable.
- No replacement of licensed equities, futures, tick, or L2 data.
- No dependence on a local SQLite source database or network access.

