# MFT Testnet operations runbook

## Scope

This runbook operates the Wave 2 medium-frequency execution loop at
`L2_DRY_RUN_ONLY`. It covers Paper preview, explicitly authorized Binance
Futures Testnet submission, recovery, read-only observation, and evidence
capture. It does not authorize mainnet, Micro-Live, real capital, automated
mandate approval, kill-switch reset, or credential management.

The five safety flags remain fixed:

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

`ALLOW_ORDER_SUBMISSION=false` is the real-capital boundary. A human may still
explicitly invoke the isolated Testnet worker with `--testnet-submit`; the
intent, venue adapter, mandate, and receipt must all remain Testnet-only.

## Authorities required before a cycle

Every execution cycle needs all of the following. Missing, stale, malformed,
or inconsistent inputs block the cycle.

1. A publishable market cursor with a valid append-only chain and no unresolved
   sequence gap.
2. A completed L2 scheduler checkpoint owned by the current fenced leader.
3. A Wave 1 PAPER workflow receipt, validation artifacts, forward evidence,
   portfolio policy, and market snapshot bound into one MFT intent hash.
4. An append-only execution inbox with no unresolved `UNKNOWN` command or order.
5. A current human-signed Testnet mandate and matching validation attestation.
6. A non-tripped authoritative kill-switch.
7. A versioned runtime risk policy and current account/market inputs. Risk
   values come from those reviewed artifacts; operators must not invent or
   override limits in a command or console.

Only `BINANCE_FUTURES_TESTNET` and an adapter reporting `venue_mode=testnet`
are accepted. The production worker never falls back to a mainnet URL.

## Preview first

Run from the repository root. Preview opens no network-capable adapter and does
not read Testnet credentials:

```powershell
rtk py -3.14 tools/testnet_execution_service.py `
  --intent <intent-json> `
  --authority <authority-json> `
  --mandate <testnet-mandate-json> `
  --attestations <attestations-json> `
  --kill-switch <kill-switch-db> `
  --command-db <command-db> `
  --recovery-db <recovery-db>
```

A successful preview returns `PREVIEW_READY`. `BLOCKED`, `RECOVERY_BLOCKED`,
`UNKNOWN`, an empty result, a timeout, or an abnormal exit is not approval.

## Explicit Testnet submission

Submission is an independent operator action. Before invoking it, verify the
preview, the current human Testnet mandate, the intent hash, the adapter mode,
and all recovery queues. Configure only the named Testnet environment variables
`BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET` through the external
runtime environment. Never place their values in a tracked file, command log,
runbook, evidence artifact, or console payload.

```powershell
rtk py -3.14 tools/testnet_execution_service.py `
  --intent <submission-authorized-testnet-intent-json> `
  --authority <authority-json> `
  --mandate <testnet-mandate-json> `
  --attestations <attestations-json> `
  --kill-switch <kill-switch-db> `
  --command-db <command-db> `
  --recovery-db <recovery-db> `
  --runtime-input <versioned-runtime-json> `
  --ledger-db <ledger-db> `
  --audit-db <audit-db> `
  --testnet-submit
```

The worker revalidates every authority immediately before submission. A
preview-only intent cannot be upgraded in place: explicit Testnet submission
has a different, hash-bound intent identity.

## Startup and recovery order

On every start or restart, preserve this order:

1. Resolve unknown orders by `client_order_id`; never blindly resend.
2. Replay the OMS event journal from zero.
3. Reconcile OMS projection, ledger positions, and Testnet venue positions.
4. Evaluate the unified risk supervisor and mandate expiry.
5. Only after all four stages pass may the inbox claim a new command.

An expired in-flight lease becomes `UNKNOWN`. A lost ACK is queried by the same
client order ID. A post-place ledger failure remains unknown until recovery
proves the venue and ledger state. No recovery path automatically resets the
kill-switch.

## Failure response

| Condition | Required state/action |
|---|---|
| Market disconnect or sequence gap | Stop publishing a trading watermark; recover from the REST snapshot authority. |
| REST 429, timeout, or incomplete coverage | Keep the cursor `BLOCKED`; do not claim the gap is filled. |
| Duplicate or out-of-order event | Idempotently ignore an exact duplicate; block time reversal or conflicting identity. |
| ACK lost | Query by deterministic client order ID; do not resend while outcome is unknown. |
| Partial fill or cancel/fill race | Journal cumulative venue truth and reconcile the incremental fill before any new risk. |
| Ledger/event projection failure | Keep the command/order `UNKNOWN`, latch or retain the applicable risk stop, and require recovery. |
| Process crash with an expired lease | Fence the stale worker, mark the command unknown, and block the next command. |
| Mandate expiry or missing signature | Halt new risk. AI and the console have no approval or recovery authority. |
| Serious reconciliation, margin, or unknown-order fault | Latch the kill-switch and escalate to manual review. |

The emergency path is separate from the normal worker and is Testnet-only. It
may emit only provably exposure-reducing MARKET `reduce-only` legs. If the
current venue position for any leg is unknown, emit no order. Partial emergency
failure keeps the kill-switch latched and requires manual review.

## Read-only observation

The console exposes two GET-only contracts:

- `/api/v1/console/mft-execution` — full v1 snapshot.
- `/api/v1/console/mft-execution/delta?cursor=<cursor>` — append-only control
  events after a snapshot cursor.

If a delta returns `RESNAPSHOT_REQUIRED`, discard local incremental state and
fetch a new full snapshot. A missing/corrupt/unknown-schema/stale store is
`UNAVAILABLE/BLOCKED`; there is no mock fallback. The console exposes no
submit, reset, or approve capability.

## Evidence and operational soak

Task 38 deterministic acceptance uses the local fake venue and canonical gate.
It proves the implementation and recovery semantics without network access or
credentials. It does not count as an operational Testnet soak.

The independent Task 39 soak must be manually started under a versioned soak
policy written before the run. Each cycle must retain market continuity,
scheduler lease, intent, OMS, fill, reconciliation, risk, TCA/parity, and
recovery-drill evidence. If credentials are absent or an operator has not
explicitly started Testnet submission, report
`IMPLEMENTATION_ACCEPTED_SOAK_PENDING` / `OPERATIONAL_SOAK_PENDING`; never infer
success from an empty, timed-out, 429, or disconnected run.

Completing a Testnet soak does not authorize Micro-Live. Micro-Live remains
blocked on external identity/mTLS, an independent signing authority, process
permission isolation, sufficient forward evidence, a human real-money mandate,
and a separate Go/No-Go.

### System-derived mandate ceiling

Operators do not invent or retype risk amounts. Before human approval, generate
an unsigned proposal from the exact submission-requested intent and versioned
runtime risk policy used by the Testnet worker:

```powershell
rtk py -3.14 -m tools.testnet_mandate_proposal `
  --intent <submission-authorized-testnet-intent-json> `
  --runtime-input <versioned-runtime-json> `
  --out <unsigned-testnet-mandate-proposal-json>
```

The proposal binds `intent_hash`, the full runtime-input hash, the normalized
`RiskLimits` hash, and the risk-policy version. Its single-order, gross, and
net amounts come from those machine-enforced limits. It is deliberately
`HUMAN_REVIEW_REQUIRED`, has an empty `approved_by`, is already expired, and
grants no execution, live-order, or Micro-Live authority.

The human operator may approve the proposed limits or make them smaller, then
set the operator identity and validity window in a separate mandate file. The
Testnet worker independently recomputes the same ceiling from its runtime input
on every submission and rejects a wider instrument scope or larger amount
before reading Testnet credentials. It repeats the comparison when loading the
authority snapshot, so changing the mandate after worker setup also fails
closed. A proposal generated outside the intent validity window is invalid.

No current production intent/runtime pair is checked into the repository by
design. Until the external operator supplies those non-secret authority files,
there is no honest numerical proposal and Task 39 remains
`OPERATIONAL_SOAK_PENDING`.

### Task 39 preregistered policy and evidence authority

The versioned policy is `config/mft_testnet_soak_policy.json`. Version 1 fixes
the window before any operational start: seven continuous days, one hourly
cycle, at least 168 completed cycles, no failed cycle, and no gap longer than
90 minutes. The numerical limits are explicitly
`PREREGISTERED_UNCALIBRATED_ENGINEERING_LIMITS`; passing them is Testnet
engineering evidence, not production calibration.

`tools.mft_testnet_soak` is an evidence recorder, not an execution driver. It
has no submit switch, credential loader, network client, exchange adapter, or
mainnet mode. It accepts exactly one mutation per invocation:

1. an operator-created start receipt that binds the policy hash, a Testnet
   mandate hash, a credential-reference hash, and a human-authorization
   receipt hash; or
2. one cycle receipt that binds market continuity, scheduler lease, intent,
   OMS, fill, reconciliation, risk, TCA, parity, and recovery-drill artifacts.

The start receipt is deliberately classified
`OPERATOR_ASSERTED_TESTNET_ONLY`, not strong identity. The recorder cannot
create or upgrade it. Credential values and mandate contents must never be
placed in either receipt. Each cycle artifact is named by a canonical relative
path beneath the operator-selected evidence root; the recorder opens the real
file, rejects symlinks/traversal/missing files, recomputes SHA-256, and only
then appends the receipt to `data/cache/mft_testnet_soak.db`.

Before an operator start, publish the honest pending projection without any
credential or network access:

```powershell
rtk py -3.14 -m tools.mft_testnet_soak `
  --policy config/mft_testnet_soak_policy.json `
  --store data/cache/mft_testnet_soak.db `
  --evidence-root data/cache/mft_testnet_soak_evidence `
  --status-out evidence/mft_testnet_soak_latest.json
```

An authorized operator starts the window in a separate invocation by adding
`--start-receipt <operator-secured-start-receipt.json>`. Each completed cycle
is then recorded with `--cycle-receipt <cycle-receipt.json>`. The cycle receipt
must use the same `run_id`, `operator_start_id`, and policy hash as the start
receipt. Empty intent/fill/submission sets, 429, timeout, unknown OMS state,
failed recovery drill, threshold breach, evidence hash mismatch, cycle gap,
chain corruption, or any mainnet submission count produces
`OPERATIONAL_SOAK_BLOCKED`, never PASS.

Continuity includes the interval from the operator start to cycle zero. Cycle
start/completion times must advance monotonically, and no individual cycle may
span longer than the registered maximum gap; a burst of late receipts or one
artificially long cycle therefore cannot satisfy the seven-day window.

The only successful terminal status is `OPERATIONAL_SOAK_PASSED`, and its
projection always retains `micro_live_ready=false`,
`live_order_authority=false`, and all five L2 safety flags. It does not modify
Wave 2 or Wave 3 acceptance artifacts automatically; an independent human
review must decide whether to record the operational result.
