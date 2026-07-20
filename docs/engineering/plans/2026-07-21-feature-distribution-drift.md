# Plan: PIT Feature Distribution Drift

1. Reuse the existing PIT trailing-window KS kernel rather than adding a second
   statistical implementation.
2. Aggregate a declared feature universe with explicit missing/extra-feature
   handling and preserve per-feature evidence.
3. Add deterministic unit tests and retain the existing no-network/no-order
   governance boundary.
4. Update the living state and roadmap without changing the top-tier score or
   claiming operational scheduling that does not exist.
