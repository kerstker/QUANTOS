# Data Distribution Drift Implementation Plan

1. Add red tests for the pure two-window KS contract and its governance
   boundary.
2. Implement the fail-closed detector with deterministic diagnostics.
3. Read real close-to-close series from the existing PIT data port and surface
   a compact read-only monitoring row; keep synthetic feature rows labeled.
4. Run focused tests, update the living state/roadmap, then run the full gate.
