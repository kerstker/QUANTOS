# QUANTOS — Agent Compatibility Guide

This file is a compact compatibility entry point for tools that automatically look for `CLAUDE.md`. The complete operating policy is [AGENTS.md](AGENTS.md); do not maintain a second long-form system inventory here.

The current guarded research-runner adapter is
`services.research_runner.main:app`; do not restore the retired nested runner
entry point.

## Read first

1. [AGENTS.md](AGENTS.md) — safety boundary and agent operating rules.
2. [docs/README.md](docs/README.md) — documentation authority and navigation.
3. [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) — append-only implementation history and current handoff.
4. [docs/QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md](docs/QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md) — current delivery sequence.
5. [evidence/validation_report.json](evidence/validation_report.json) — latest canonical validation result.

## Non-negotiable boundary

QUANTOS defaults to `L2_DRY_RUN_ONLY`:

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

- Do not access or expose `key/`, broker credentials, passphrases, or signed mandates.
- Do not submit Mainnet orders, weaken risk gates, fabricate evidence, or treat timeout/429/empty/error results as approval.
- Keep research, paper, Testnet, and Mainnet isolated.
- `services/` is deterministic and network-free; public-data networking belongs at guarded `tools/` boundaries.
- Use point-in-time data and never introduce lookahead.
- Write a failing test before changing safety-critical behavior.

Testnet connectivity exists, but it does not grant candidate admission or Mainnet authority. Strategy intent and risk permission remain separate.

## Canonical commands

Run from the repository root:

```bash
# Windows workstation
py -3.14 scripts/validate.py

# CI / portable equivalent
python scripts/validate.py

# Repository layout and public-doc checks
python scripts/check_repository_hygiene.py
```

Do not use bare `pytest` as the final gate. Focused tests may use `py -3.14 -m pytest <paths>`, followed by the full validation command.

## Change workflow

```text
current state → design/spec when needed → failing test → smallest safe change
→ focused regression → governance/hygiene checks → full validation
→ evidence refresh → intentional commit → review/push
```

Preserve unrelated dirty work. Historical engineering plans and audit snapshots are evidence, not current instructions; when they conflict with machine evidence or current docs, follow the authority order in `docs/README.md`.
