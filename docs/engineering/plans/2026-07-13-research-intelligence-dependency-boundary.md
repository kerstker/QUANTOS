# Service Dependency Boundary Progress

**Date:** 2026-07-13  
**Risk:** P2-4  
**Scope:** L2-safe architecture hardening only; no live authority or trading behavior changes.

## Goal

Shrink the service-domain dependency SCC one reviewed edge at a time while
preserving behavior and making any graph growth fail the unified gate.

## Completed

- Added `ResearchSearchRuntime` to the deterministic research-session core.
- Moved the supported composition entry to
  `services/intelligence/research_session.py::run_research_session`.
- Fixed the composition to the guardrailed `ResearchAgentLoop` and existing search
  implementations; caller runtime overrides fail closed.
- Migrated repository tools and end-to-end callers to the supported entry.
- Added an AST governance rule forbidding all `services.research` imports of
  `services.intelligence`.
- Verified Ruff and the related research/intelligence/data/governance suite:
  `752 passed, 1 skipped`.
- Added a deterministic production service import graph, exact known-cycle policy,
  unresolved dynamic-import baseline, tamper-evident snapshot, and unified-gate /
  supply-chain / top-tier wiring.
- Moved the canonical `CostModel` to `quantos/contracts`, retained the research
  compatibility export, and removed `portfolio -> research` without changing math.
- Reduced the graph from 26 to 25 edges, the SCC from six to five domains, and
  cyclic direct edges from 14 to 12. Intelligence and portfolio are outside the SCC.
- Moved paper fill contracts to `quantos/contracts` and reconciliation plus impact
  ownership to execution, retaining identity-preserving research exports. The graph
  is now 24 edges, a four-domain SCC, and 10 cyclic edges; execution is outside it.
- Added a fail-closed, one-time-configured `RiskConsolePort`, moved concrete risk
  composition to `services/console`, and removed `research -> risk`. Risk is outside
  the SCC without weakening any risk gate or safety primitive.
- Moved the mutating promotion HTTP composition root from governance to the explicit
  `services/promotion_api` domain. Governance no longer imports alpha or research;
  the OIDC/mTLS/MFA/RBAC boundary and approval semantics are unchanged.
- Proved the old policy stale before tightening it. The current graph has 14 domains,
  27 direct cross-domain edges, one two-domain SCC (`alpha`, `research`), and only
  two cyclic direct edges.
- Verified the completed slice through the unified fail-closed gate: 2,153 main
  tests passed with one skip, 902 live/trading/tools tests passed, 68 frontend tests
  plus typecheck/build passed, and the governance scanner found zero violations in
  260 files.
- Extracted performance, IC, regime, DSR, and gate primitives into a domain-neutral
  `services/validation` owner with identity-preserving research compatibility exports.
  Moved factor-set selection into alpha and switched alpha cost use to contracts.
- Proved the last two cycle permissions stale before setting `known_cycle_edges=[]`.
  The current production graph has 15 domains, 29 direct edges, zero cyclic SCCs,
  and zero cyclic direct edges. Focused validation/alpha/governance tests: 185 passed.

## Honest Boundary

This closes the cyclic-dependency half of P2-4 and makes every new cycle fail closed.
It does not close P2-4 as a whole: the research domain is oversized, and the console
API remains roughly 4,900 lines.

## Next Ordered Slices

1. Split `services/research/console_api/main.py` by read-only router, keeping each
   router below 800 lines and preserving the frontend contract tests.
2. Shrink the remaining research package behind explicit ports/adapters without
   recreating service cycles.
3. Keep composition roots outside governed domains and keep the cycle policy empty.
