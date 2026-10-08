# Catalog-bound PBO evidence implementation plan

1. **Red tests**
   - Add contract/verifier tests for determinism, replay, matrix tamper, PBO tamper, duplicate
     candidates and invalid numeric input.
   - Add evaluator tests proving raw scalar PBO is rejected, valid evidence participates, and
     invalid/foreign evidence fails closed.
   - Add lifecycle tests for missing evidence and same-count trial-set substitution.
   - Add experiment-registry tests proving semantic replay is required after content-hash checks.

2. **Contract and shared verifier**
   - Add frozen `BatchPboEvidence` and alignment enum under `quantos/contracts`.
   - Add `services/validation/pbo_evidence.py`, wrapping the existing `pbo_cscv` implementation.

3. **Batch builder and evaluator**
   - Refactor the existing `batch_pbo` alignment/block logic to return the evidence object.
   - Replace evaluator `pbo=` with `pbo_evidence=` and bind evidence/trial-set hashes into the
     returned `AlphaEvaluation` and reproducibility hash.
   - Update both `AlphaFactory` and `ResearchAgentLoop`.

4. **Load-bearing guards and persistence verification**
   - Require verified evidence and an exact current catalog trial-set hash in lifecycle guards.
   - Extend immutable evaluation verification to parse and semantically replay PBO evidence.
   - Keep unassessed artifacts recordable but not promotion-eligible.

5. **Read-only surfacing and governance**
   - Add redacted PBO provenance metadata to alpha-library responses.
   - Add new files to supply-chain manifests and service-boundary tests.
   - Update `PROJECT_STATE` and the institutional completion plan honestly.

6. **Verification and publication**
   - Run focused alpha/validation/registry/console/governance tests.
   - Refresh service graph, supply-chain and top-tier artifacts.
   - Run `python scripts/validate.py`, review the complete diff and secret scan, then commit, push,
     wait for both CI jobs and merge only at the reviewed head SHA.
