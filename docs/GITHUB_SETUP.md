# GitHub repository settings

Status: **CURRENT CHECKLIST**  
Repository: `kerstker/QUANTOS`

These settings complement the versioned README, CI, CODEOWNERS, templates, and security policy. Repository settings must never imply Mainnet or investment readiness.

## About panel

Recommended description:

```text
Safety-critical AI quantitative research platform — PIT data, robust validation, paper/shadow workflows, and fail-closed governance. L2_DRY_RUN_ONLY.
```

Recommended topics:

```text
quant-research
algorithmic-trading
backtesting
alpha-research
risk-management
point-in-time-data
paper-trading
fastapi
react
typescript
research-platform
```

Do not use `live-trading`, `automated-trading-bot`, or performance-marketing topics while Mainnet and formal candidate admission remain blocked.

Recommended website field: leave blank unless a maintained, access-controlled console/documentation site exists.

## Visibility and features

- Keep the repository **Private** unless a separate secret/history/license review explicitly approves publication.
- Disable Wiki so `docs/` remains the only documentation source.
- Enable private vulnerability reporting/security advisories.
- Issues and Discussions are optional; never disclose security details or credentials publicly.
- Do not enable GitHub Pages from runtime evidence or internal reports without a publication review.

## Main branch protection

Protect `main` with a ruleset:

- require a pull request;
- require at least one approving review;
- require CODEOWNERS review for safety-critical paths;
- require conversation resolution;
- block force pushes and branch deletion;
- require the branch to be up to date;
- do not allow bypasses except an explicitly audited break-glass owner.

Required status checks from `.github/workflows/validate.yml`:

```text
backend gate (governance scanner + all test suites)
backend coverage regression floor
frontend (typecheck + build + test)
```

The Sigstore workflow is separate supply-chain evidence and should retain least-privilege permissions and pinned action SHAs.

## Releases

Use a release only for a reproducible research-platform milestone. Release notes must include:

- source commit and validation evidence;
- readiness (`L2_DRY_RUN_ONLY` unless a separately governed process changes it);
- known blockers and manual/unverified requirements;
- data/evidence provenance;
- explicit statement that the release grants no trading authority.

Do not publish credentials, runtime databases, raw licensed data, signed mandates, local state paths, or internal identity artifacts.

## Repository files that enforce the policy

- [README.md](../README.md)
- [SECURITY.md](../SECURITY.md)
- [CONTRIBUTING.md](../CONTRIBUTING.md)
- [.github/CODEOWNERS](../.github/CODEOWNERS)
- [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md)
- [.github/workflows/validate.yml](../.github/workflows/validate.yml)
- [scripts/validate.py](../scripts/validate.py)

After changing the About panel or ruleset, capture only non-secret settings evidence. Never copy tokens or private key material into screenshots or reports.
