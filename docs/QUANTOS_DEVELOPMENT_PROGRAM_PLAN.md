# QUANTOS — Full Development Program Plan

Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](../QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14, §1 safety, §8 rejection reasons, §9 quant validation,
§15 Definition of Done).

**Current state (Phase 0 audit, verified):** ~10% built; safety posture strong
and code-enforced. See [`README.md`](../README.md) and [`CLAUDE.md`](../CLAUDE.md).

**Operating principle:** tests-first, smallest safe change, research/paper/live
isolation, and live trading provably impossible without multi-step human
approval — at every phase.

---

## 1. Critical path

```
0.5 → 1 → 2 → 3 → 5 → 6 → 7 → 8 → 9 → 10 → 13 → 14
            └─ 4 (backtest) runs parallel to 2 but is a mandatory feeder into 5
```

The credibility bottleneck is the data→feature→validation chain (2 → 3 → 5):
until real point-in-time data exists, phases 4–7 run on fixtures and are **not
production-ready** (enforced at gates G4–G7).

## 2. Parallel lanes (after the serial roots 0.5 → 1)

| Lane | Phases | Owns |
|---|---|---|
| A — research-science spine | 2 → 3 → (5) | `services/data/**` |
| B — backtest/alpha/portfolio | 4 → 5 → 6 → 7 | `services/research/backtest`, `services/alpha`, `services/portfolio` |
| C — trading/risk/execution | 8 → {9 ‖ 11} → 10 | `services/risk`, `services/trading`, `paper_trading` |
| D — frontend | 12 (mock-first from the start) | `apps/research-console/src` |

Shared hotspot: `services/research/console_api/main.py` is edited by many
phases — consider decomposing it into per-domain routers in Phase 1.

## 3. Phases

| # | Phase | Effort | Closes |
|---|---|---|---|
| 0.5 | Safety & Governance Baseline | M | reproducibility, fail-closed CI, machine-enforced §1 |
| 1 | Architecture Contracts & Domain Models | L | one canonical `RejectionReason` (§8), `OrderState` (§7), config §1.2 |
| 2 | Data Layer (point-in-time / bitemporal moat) | XL | **Gap 1 — data moat** |
| 3 | Feature Layer (leakage-free) | L | static + dynamic leakage detection |
| 4 | Research OS & Backtest Engine | XL | deterministic backtest, real costs, PnL reconciliation |
| 5 | Validation Gate (anti-overfitting) | L | **Gap 2 — anti-self-deception** (Deflated Sharpe, PBO, purged/embargoed CV) |
| 6 | Alpha Factory | L | governed alpha lifecycle |
| 7 | Portfolio Layer | XL | factor risk model + optimizer, cost-aware |
| 8 | Risk Layer (pre/post-trade) | L | all 20 §8 reasons + kill-switch + enforced limits |
| 9 | Paper Trading | L | real PnL reconciliation (replaces `notional*0.001`) |
| 10 | Execution Boundary | L | broker adapter abstraction; live disabled |
| 11 | AI Agent Layer | XL | **Gap 3 — guardrailed AI research loop** |
| 12 | Frontend Console | XL | wire 25+3 pages to real backends; truthful provenance |
| 13 | Monitoring & Audit | L | tamper-evident audit chain |
| 14 | Deployment Governance | L | layered env config; live impossible in-repo |

## 4. Cross-cutting invariants (every phase, every gate)

- **CT-1 Data integrity** — point-in-time / no-lookahead by construction.
- **CT-2 Anti-overfitting** — net-of-cost metrics; known-overfit MUST fail;
  injected leakage MUST be caught.
- **CT-3 Reproducibility** — content-addressed hashes; versioned datasets/configs.
- **CT-4 Safety & live-isolation** — governance scanner in CI; each phase extends
  `SCAN_DIRS` to cover its new package.
- **CT-5 AI guardrails** — agents never reach live/credentials, never self-approve,
  never bypass validation (negative-security tests written first).

## 5. Milestone gates (human sign-off — blueprint §15)

Each phase ends at a hard gate (G0.5 … G14). Every gate requires: tests-first
passing, governance scanner green over the package allow-list (with a planted
positive control), no live capability enabled, no secrets/broker SDK, and a
changed-files / tests-run / risks / rollback summary. The next phase is not
authorized until sign-off is recorded.

## 6. Adversarial-review hardening (applied to the gates)

These corrections came from a completeness critique and are folded into the
gate acceptance criteria above:

- **G0.5 scanner scope** — assert coverage by *package allow-list* (incl.
  `services/risk`), not `files_scanned>0`; require a planted positive control in
  each scanned tree. *(Implemented in Phase 0.5.)*
- **G4** — backtest must not be signed off on lookahead-contaminated fixtures;
  it must pass against the real Phase-2 point-in-time store with a
  future-knowledge-unreachable test.
- **G5** — Deflated Sharpe must consume a persistent **total trial count**; a
  single-regime strategy and a capacity-failing strategy must NOT pass.
- **G8** — name the single enforced risk chokepoint; add a
  `test_advisory_limit_now_blocks` regression and a kill-switch first-short-circuit
  test.

## 7. Phase 0.5 — first increment (in progress)

Status: **landed** (commits establishing the baseline + the fail-closed
validation harness). Delivered:

1. `.gitignore` + initial git baseline (dependencies/artifacts excluded).
2. `scripts/validate.py` — fail-closed gate (governance scanner + all runnable
   pytest suites), proven to exit non-zero on a planted violation.
3. Governance scanner repaired (repo-root anchored, covers the real
   safety-critical trees, secret-literal credential detection) +
   `tests/governance/` (coverage + positive controls + no-false-positive +
   quarantine checks).
4. Broken `pre_trade_risk` quarantined (preserved for Phase 8).
5. `pyproject.toml` / `requirements*.txt` (pinned), `CLAUDE.md`, and CI.
6. README/docs reconciled to verified reality.

**G0.5 acceptance:** `python scripts/validate.py` green and fail-closed; zero
domain-logic change; human sign-off authorizes **Phase 1**.
