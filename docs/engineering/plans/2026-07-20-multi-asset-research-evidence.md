# Multi-Asset Research Evidence Implementation Plan

1. Add a stable FX factor-spec contract and bundle-to-panel adapter.
2. Register the versioned FX universe plus momentum and low-vol factors in the
   IC panel registry.
3. Add failing tests for PIT validation, deterministic research replay,
   tamper rejection, semantic assessment, clean-checkout operation, and the L2
   boundary.
4. Implement the research evidence generator and independent governance
   verifier.
5. Replace the path-existence top-tier checker with the semantic checker and
   regenerate the assessment artifacts.
6. Update the live project state, regenerate derived governance/supply-chain
   evidence, run focused tests, then run `python scripts/validate.py`.

