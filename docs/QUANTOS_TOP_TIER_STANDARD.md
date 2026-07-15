# QUANTOS Top-Tier Standard

Authoritative design: `docs/engineering/specs/2026-07-10-top-tier-ai-quant-system-design.md`.

## Authority

- `config/top_tier_requirements.json` declares stable requirement IDs and targets.
- `services/governance/top_tier_assessment.py` derives current status from evidence.
- `python scripts/assess_top_tier.py` generates the canonical matrix and JSON evidence.
- `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md` is generated output, not a hand scorecard.
- `python scripts/validate.py` fails when generated assessment artifacts drift.

## Safety Boundary

The system remains `L2_DRY_RUN_ONLY`. Top-tier research maturity never enables live trading. Production-trading requirements may be `NEEDS_EXTERNAL`, `POST_L2`, or `MANUAL_EVIDENCE_REQUIRED`; none authorize credentials, broker connectivity, order submission, or AI direct trading.

## Maturity

- `M0 ABSENT`: no implementation.
- `M1 DEMONSTRATION`: design, mock, synthetic, or scaffold only.
- `M2 REAL_SINGLE_NODE`: real single-node capability with basic tests.
- `M3 INSTITUTIONAL_RESEARCH`: evidence contract, fail-closed boundary, audit, and reproducibility.
- `M4 PRODUCTION_READY`: strong identity, isolation, observability, recovery, capacity, and drills.
- `M5 TOP_TIER_CONTINUOUS`: sustained SLO, independent validation, red-team, fault injection, drift detection, and recovery evidence.

## Evidence Contract

Requirements select from six evidence types: implementation, verification, data, operation, governance, and reproducibility. A checker success below the target maturity is `PARTIAL`, not `PASS`. External and post-L2 requirements never auto-pass.

Risk recovery evidence is scoped to the state classes and failure injections named in its artifact. It does not imply high availability, venue recovery, production disaster recovery, or live-trading readiness.

An offline SBOM and pinned-input baseline may establish M3 supply-chain discipline, but it cannot satisfy M4 without externally sourced vulnerability evidence and identity-backed artifact signatures verified outside the build process.

A governed offline model registry, real agent-boundary red-team run, and challenger
decision can establish M2 model-risk evidence. They cannot satisfy M3 while raw
evaluation data is not retained, no runtime LLM prompt surface exists, review is
not bound to strong external identity, and longitudinal evaluation history is absent.

## Domains

The registry must cover data moat, alpha research, validation/model risk, portfolio construction, risk management, execution/microstructure, AI agents, governance/security, platform engineering, reliability/operations, and frontend console.

## Verdicts

- Any open red line: `NOT_TOP_TIER`.
- No red-line failure and every Tier A requirement at target: `TOP_TIER_RESEARCH`.
- Otherwise: `IN_PROGRESS`.

`TOP_TIER_RESEARCH` does not mean production trading is approved.

## Change Discipline

1. Add or change a target in the registry.
2. Add or strengthen a named evidence checker and tests.
3. Regenerate assessment JSON and matrix.
4. Run focused tests and the canonical validation gate.
5. Never edit maturity results directly.
