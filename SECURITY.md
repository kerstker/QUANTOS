# Security Policy

QUANTOS has two security domains: protection of credentials and protection against unauthorized trading. Both fail closed.

## Supported security posture

The supported repository posture is `L2_DRY_RUN_ONLY`.

- Research, backtest, dry-run, paper, and explicitly approved Testnet workflows may exist.
- Mainnet, real capital, AI-directed trading, and unapproved order submission are blocked.
- Testnet connectivity is not evidence that a strategy is admitted and is not permission to use Mainnet.

The default flags remain:

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

## Credential handling

- Keep credentials, API keys, passphrases, tokens, attestations, and signed mandates outside Git.
- Use environment variables or the ignored local `key/` directory only when an approved operator workflow explicitly requires them.
- Never paste credential values into issues, pull requests, chat transcripts, logs, test fixtures, screenshots, or evidence JSON.
- Never enumerate or print the contents of `key/` during routine diagnostics.
- Runtime databases and raw market data belong under ignored state roots such as `data/cache/`, not in commits.
- Public-data keys must be read-only. Account, withdrawal, transfer, or custody permissions are outside the research boundary.

The repository hooks reject tracked secret paths and common credential patterns. Install them on each workstation:

```bash
sh scripts/install-hooks.sh
git config --get core.hooksPath
```

Hooks are defense in depth; they do not replace review, secret scanning, or credential rotation after exposure.

## Trading and governance boundary

- Non-human actors cannot approve candidates, reset protected kill-switch state, access credentials, or authorize Mainnet orders.
- Strategy intent is separate from risk permission.
- Attestation, validation, mandate, pre-trade risk, ledger, confirmation, reconciliation, and audit controls must not be bypassed.
- Missing, stale, malformed, empty, timed-out, rate-limited, or unknown evidence is a rejection—not a successful review.
- `services/` is a network-free deterministic core. Reviewed read-only acquisition belongs in `tools/`; exchange adapters remain isolated under `live/`.
- Point-in-time and knowledge-time contracts are security controls against lookahead.

These boundaries are enforced by `tests/governance/`, dedicated live/Testnet boundary tests, the governance scanner, supply-chain evidence, and `scripts/validate.py`.

## Vulnerability reporting

Do not disclose a suspected bypass or credential exposure in a public issue.

Use GitHub's private security advisory flow for this repository or contact the owner listed in [.github/CODEOWNERS](.github/CODEOWNERS). Include:

- affected commit and component;
- reproduction steps using non-secret/test data;
- expected and observed fail-closed behavior;
- which invariant is affected;
- suggested containment if known.

If a credential may have been exposed, revoke and rotate it first. Do not wait for a code fix.

## Scope

Security fixes target `main`. Historical branches, archived evidence, generated reports, and local worktrees are not separate supported releases.
