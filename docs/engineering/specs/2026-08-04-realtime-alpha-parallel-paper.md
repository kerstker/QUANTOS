# Realtime Alpha parallel engineering-paper admission

## Decision

Realtime Alpha research and realtime paper observation run in parallel.

- At three days, an `EVALUATED` candidate may enter the isolated engineering-paper ledger.
- At thirty days, the unchanged DSR, PBO, locked holdout, regime, doubled-cost, drawdown, and capacity gates may establish formal validation.
- Ninety-day maturity remains a graduation requirement for formal Production Paper; it is not a prerequisite for starting engineering observation.

## Authority boundary

The early track is `AUTONOMOUS_REALTIME_ENGINEERING_SHADOW`. It is append-only and receives public market data only. Every admission, decision, realization, and report keeps:

- `promotion_eligible=false`
- `forward_evidence_credit=false`
- `deployment_eligible=false`
- `live_allowed=false`
- `order_submission_allowed=false`

It cannot write to the official Paper execution authority. Formal fills remain exclusively authorized by `ALPHA_PRODUCTION_WORKFLOW`.

## Evidence semantics

`evaluation_status` is now separate from workflow `status`. This prevents an evaluated exploratory candidate from being indistinguishable from an insufficient-data candidate.

The engineering-paper admission requires:

1. a valid, fresh, hash-bound discovery handoff;
2. `data_admission.exploration_allowed=true`;
3. `evaluation_status=EVALUATED`;
4. a positive conservative shadow size derived from retained executable depth;
5. the unchanged L2 safety boundary.

Thirty- and ninety-day conditions are emitted as `graduation_blockers`, not admission blockers. No historical microstructure is synthesized, and no engineering result receives formal forward credit.
