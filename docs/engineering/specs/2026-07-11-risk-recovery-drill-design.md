# QUANTOS Risk Recovery Drill Design

## Goal

Close the `RISK.RECOVERY.DRILL` red line with a repeatable offline drill that
proves critical risk state survives a process restart, continues to block new
intent when halted, and fails closed when persisted state is damaged.

## Safety Boundary

- Remain `L2_DRY_RUN_ONLY`.
- Never connect to a broker, venue, network service, credential store, or order
  submission path.
- Never read from or write to active `data/cache`, `key`, or configured live
  state paths.
- Operate only inside a caller-supplied empty directory or an internally
  created temporary directory.
- Use deterministic synthetic state solely to exercise existing persistence
  and risk-gate behavior; synthetic drill state is never represented as market
  or production evidence.
- Any ambiguous path, state mismatch, unreadable artifact, unexpected gate
  result, audit-chain failure, or RTO breach fails closed.

## Scope

The critical recovery bundle contains three independently verifiable state
classes:

1. A persisted, globally tripped `KillSwitchState` with an incident identifier
   and reason.
2. A SQLite `LiveLedger` containing one deterministic submitted order and one
   deterministic fill, sufficient to reconstruct the expected position.
3. A persisted hash-chained audit log containing the drill setup and halt
   events.

Mandates, incident workflow, venue reconciliation, TCA, broker state, and
external signing are outside this drill. They require separate requirements or
external infrastructure and must not be implied by a PASS result.

## Architecture

Add `tools/risk_recovery_drill.py` with an offline orchestration entry point and
a thin CLI. The orchestrator owns only paths below the isolated drill root and
uses existing QUANTOS persistence APIs instead of duplicating their formats.

The primary interface is:

```python
def run_risk_recovery_drill(
    *,
    drill_dir: str,
    rto_ms: int,
    observed_at_ms: int,
) -> dict[str, Any]:
    ...
```

`observed_at_ms` is caller supplied so evidence tests remain deterministic.
Elapsed duration is measured around the recovery operation with
`time.perf_counter()` and is used only for operational evidence, never for
trading or research logic.

The CLI writes `evidence/risk_recovery_drill_latest.json` atomically only after
all assertions pass. A failed drill exits nonzero and must not replace prior
PASS evidence.

## Drill Flow

1. Validate that the drill directory is absent or empty and is outside active
   state and credential paths.
2. Create the critical state bundle through existing persistence APIs.
3. Record canonical SHA-256 digests and semantic summaries for each state
   class.
4. Close all SQLite and file-backed objects to model process termination.
5. Reopen the bundle through fresh objects, with no in-memory state retained.
6. Assert that the kill switch remains globally tripped with the same incident
   identifier and reason.
7. Construct a deterministic dry-run intent and evaluate it through the
   existing `PreTradeRiskGate`; assert rejection is attributable to the active
   kill switch.
8. Assert that reopened ledger orders, fills, and reconstructed positions match
   the pre-restart semantic summary.
9. Verify the reopened audit chain and compare its terminal hash with the
   pre-restart commitment.
10. Run fault injection against isolated copies: corrupt the kill-switch JSON
    and tamper with an audit record. Assert that corrupted kill-switch state is
    interpreted as tripped and that audit verification rejects the tampered
    chain.
11. Enforce the configured RTO and publish evidence only if every assertion
    passes.

## Evidence Contract

The evidence document has `schema_version: 1` and records:

- `verdict: "PASS"`
- `drill_mode: "ISOLATED_OFFLINE"`
- `observed_at_ms`, `elapsed_ms`, `rto_ms`, and `rto_met`
- normalized drill-relative artifact names, never active absolute paths
- `recovered_state_count: 3`
- pre-restart and post-restart SHA-256 digests
- order, fill, and position semantic summaries
- `kill_switch_persisted: true`
- `gate_rejected_while_tripped: true`
- the exact gate rejection code
- `ledger_recovered: true`
- `audit_chain_verified: true`
- `corrupt_kill_switch_failed_closed: true`
- `tampered_audit_detected: true`
- `active_state_untouched: true`

The document must not contain secrets, environment values, broker identifiers,
API keys, passphrases, or machine-specific active paths.

## Failure Semantics

- Missing, invalid, non-empty, symlinked, or active-state drill target: fail.
- Non-positive RTO or invalid observed timestamp: fail.
- State creation, close, reopen, or schema validation error: fail.
- Kill switch not tripped after restart or changed incident metadata: fail.
- Risk gate permits the deterministic intent or rejects for an unrelated
  reason: fail.
- Ledger order, fill, position, or digest mismatch: fail.
- Audit chain or terminal commitment mismatch: fail.
- Corrupt kill-switch state does not fail closed: fail.
- Tampered audit chain is accepted: fail.
- Elapsed duration exceeds RTO: fail.
- Any failure leaves existing PASS evidence untouched.

## Assessment Integration

Add a `risk_recovery_evidence` checker to
`services/governance/top_tier_assessment.py`. The checker accepts only schema
version 1 PASS evidence with every recovery and fault-injection invariant true,
a positive recovered state count, and `elapsed_ms <= rto_ms`. Missing,
malformed, stale-shaped, or threshold-violating evidence is `FAILED`, not
manual evidence.

Change `RISK.RECOVERY.DRILL` in `config/top_tier_requirements.json` from
`manual_evidence` to `risk_recovery_evidence`, targeting M4 and the canonical
evidence path. A successful real drill raises the capability matrix from 15/22
to 16/22 while the overall verdict remains `NOT_TOP_TIER` because strong
identity and signed supply-chain evidence remain open red lines.

## Verification

- Unit tests prove normal close-and-reopen recovery for all three state classes.
- Unit tests prove gate rejection is specifically caused by the recovered
  tripped kill switch.
- Unit tests prove corrupt kill-switch JSON fails closed.
- Unit tests prove audit tampering is detected.
- Unit tests prove active, non-empty, and symlink targets are rejected.
- Unit tests prove failed drills do not overwrite previous PASS evidence.
- Assessment tests reject missing fields, false invariants, invalid timing, and
  non-PASS verdicts.
- Governance tests prohibit network, broker SDK, credential, active-state, and
  order-submission behavior.
- The generated assessment artifacts are byte-stable after regeneration.
- `python scripts/validate.py` remains the canonical final gate.

## Honest Claim

PASS means QUANTOS has machine-verified single-node recovery evidence for the
specified offline risk-state bundle. It does not prove high availability,
multi-node failover, venue recovery, production disaster recovery, strong
identity, external signing, or live-trading readiness.
