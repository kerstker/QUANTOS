# Contributing to QUANTOS

QUANTOS is a safety-critical quantitative research platform. Contributions are welcome only when they preserve `L2_DRY_RUN_ONLY`, fail closed, and keep research evidence separate from trading permission.

## Before changing code

Read, in order:

1. [AGENTS.md](AGENTS.md)
2. [docs/README.md](docs/README.md)
3. [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)
4. The closest domain tests and any linked design/spec document

Do not use an old roadmap or audit snapshot as current truth merely because it is detailed.

## Development workflow

1. Confirm the working tree and branch. Preserve unrelated changes.
2. For new behavior, write a focused failing test first.
3. Make the smallest safe implementation change.
4. Run the focused suite with `py -3.14 -m pytest <paths>` on the Windows workstation.
5. Run repository hygiene and governance checks.
6. Run the canonical full gate before publication.
7. Update current documentation and generated evidence when their contracts require it.

Canonical gate:

```bash
# Windows workstation
py -3.14 scripts/validate.py

# Portable / CI
python scripts/validate.py
```

Do not use bare `pytest` as final evidence. The canonical gate owns test grouping, generated-artifact checks, frontend validation, and governance scans.

## Safety rules

- Keep `LIVE_TRADING_ENABLED`, `REAL_BROKER_ENABLED`, `ALLOW_ORDER_SUBMISSION`, and `ALLOW_AI_DIRECT_TRADING` false; keep `REQUIRE_HUMAN_APPROVAL` true.
- Never commit secrets, broker credentials, raw runtime data, SQLite authorities, passphrases, or signed mandates.
- Never weaken approval, risk, attestation, kill-switch, reconciliation, or audit boundaries to make a test pass.
- Never treat missing, stale, malformed, timed-out, rate-limited, or self-reported evidence as approval.
- Keep `services/` network-free. Public-data networking belongs in reviewed `tools/` fetchers and must remain read-only.
- Use point-in-time data and explicit knowledge-time semantics; no lookahead.
- Deterministic research may use pinned scientific dependencies. Randomness, clocks, and I/O must be controlled at boundaries and recorded where applicable.
- AI/non-human actors may research and recommend; they may not approve, handle credentials, or place Mainnet orders.

## Backend and frontend contracts

When a console API payload changes, update all three in one commit:

- the backend response;
- the TypeScript payload type/consumer;
- `tests/research/test_console_api_contract.py` and relevant frontend tests.

Frontend checks:

```bash
cd apps/research-console
npm ci
npm run typecheck
npm run eslint
npm test
npm run build
```

## Documentation rules

- Keep GitHub-facing and operator-facing instructions concise and current.
- Put durable current guidance in the locations listed by [docs/README.md](docs/README.md).
- Put dated design intent under `docs/engineering/specs/` and implementation plans under `docs/engineering/plans/`.
- Treat `docs/audit/`, `docs/reports/`, and older dated roadmaps as snapshots unless explicitly marked living.
- Do not create one `PLANNED.md` file per empty future directory. Update [docs/PLANNED_SCAFFOLDS.md](docs/PLANNED_SCAFFOLDS.md) instead.
- Run `python scripts/check_repository_hygiene.py` after changing curated docs or repository layout.

## Branches, commits, and pull requests

- Default branch: `main`.
- New Codex branches use the `codex/` prefix; conventional `feat/`, `fix/`, `docs/`, and `chore/` branches are also accepted for human workflows.
- Use concise conventional commit subjects, for example `docs: simplify repository entrypoints`.
- Keep commits intentional; do not combine generated evidence, unrelated refactors, and product behavior without a clear reason.
- Pull requests must explain scope, risks, evidence, and any remaining blockers.
- Required CI jobs are the backend gate, backend coverage floor, and frontend gate.

Install the optional repository hooks with:

```bash
sh scripts/install-hooks.sh
```

## Reporting security issues

Do not open a public issue for a possible credential leak or governance bypass. Follow [SECURITY.md](SECURITY.md).
