# Governed Model-Risk Baseline Design

## Goal

Create a durable, tamper-evident baseline for model cards, tool lineage, offline
evaluations, agent-boundary red-team results, and challenger decisions. The
baseline must use existing real QUANTOS artifacts and must not imply model edge,
production deployment approval, or live-trading authority.

## Real Evidence Sources

1. `data/cache/ml_stack_snapshot_fx.json`: real FX purged/embargoed walk-forward OOS；crypto 研究写入独立的 `ml_stack_snapshot_crypto.json`
   comparison of Ridge, GBM, and the selected single-factor baseline. All three
   Sharpe values are negative and the source verdict is `REFUTED_NO_EDGE`.
2. `services/intelligence/guardrails.py` and `ResearchAgentLoop`: executable
   non-human boundaries for approval, kill-switch reset, live trading, and
   credential access.
3. Implementation and source-data SHA-256 commitments supplied by the evidence
   generator.

## Contracts

Frozen contracts cover:

- model cards with owner, intended use, prohibited use, implementation digest,
  dataset reference, prompt lineage status, and tool manifest reference;
- tool manifests with allowed and prohibited action sets;
- immutable offline evaluation artifacts;
- red-team case results and aggregate reports;
- challenger reviews with distinct owner/reviewer groups and an absolute metric
  floor.

All wall-clock values are caller supplied. Non-finite metrics, duplicate case
IDs, self-comparison, and owner/reviewer group reuse fail closed.

## Registry

`services/governance/model_risk_registry.py` owns one append-only SQLite table.
Each artifact stores canonical JSON plus a stable content hash. Re-recording the
same artifact is idempotent; changing content under an existing ID raises.
Reading an uninitialized or corrupt store fails closed. Verification recomputes
every stored hash.

## Honest Maturity Boundary

This increment may establish M2 only. It cannot reach M3 because:

- the 81 MB source FX database is local and hash-identified but not retained in
  the versioned evidence bundle;
- current autonomous research uses a deterministic policy loop, not a governed
  runtime LLM prompt surface;
- challenger review separation is a process role, not externally authenticated
  identity;
- the real ML result is a null result, not a deployable model.

These limits are evidence, not failures to conceal.

## M3 Exit Conditions

1. Retain a versioned, reconstructable dataset manifest for every evaluation.
2. Register every runtime model, prompt template, tool schema, and policy version.
3. Run a broader adversarial suite including prompt injection when an LLM prompt
   surface actually exists.
4. Bind challenger review to strong external identity and separation of duties.
5. Accumulate repeated offline evaluations across model and prompt changes.

