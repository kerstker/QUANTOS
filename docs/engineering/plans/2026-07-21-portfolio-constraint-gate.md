# Portfolio Constraint Gate Implementation Plan

1. Add pure result objects and red tests for tail CVaR, firm exposure,
   capacity, crowding, malformed evidence, and deterministic composition.
2. Implement the unified fail-closed evaluator with explicit `PENDING` and
   `BLOCK` semantics.
3. Integrate it opt-in at optimizer, post-rebalance construction, and signal
   adapter boundaries; keep legacy calls byte-compatible.
4. Add the offline governance boundary test, update the living roadmap, run
   focused tests, then run the canonical gate before publication.
