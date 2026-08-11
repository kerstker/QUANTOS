# Deployment surfaces

Status: **CURRENT L2 BOUNDARY**

QUANTOS does not have one monolithic “deploy everything” command. Deployment surfaces are intentionally separated by authority and risk.

| Surface | Status | Network/order capability |
|---|---|---|
| Local research stack | Supported | Localhost console plus guarded research jobs; no exchange/order path |
| Read-only console container | Supported and reproducibility-tested | Serves the GET-only API on 8400 |
| L2 scheduled research/data jobs | Supported when explicitly registered | Approved public-data/research tools only |
| Testnet execution/soak | Guarded, candidate-dependent | Testnet only; requires attestations, mandates, gates, and operations runbook |
| Mainnet | Blocked at L2 | No authorized deployment |
| Kubernetes/Terraform/GitOps platform | Not implemented | Planned external infrastructure, not repository capability |

## Read-only console image

`console.Dockerfile` builds only the read-only API. Its build context excludes `key/`, `data/`, `live/`, tools, tests, and local environment files. The runtime is non-root and keeps all live flags disabled.

Build:

```bash
docker build --file deploy/console.Dockerfile --tag quantos-console:l2 .
```

Run with a read-only filesystem:

```bash
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --publish 127.0.0.1:8400:8400 \
  quantos-console:l2
```

Check:

```bash
curl http://127.0.0.1:8400/health
```

Expected mode is `READ_ONLY`. The minimal image intentionally has no execution stack and no credentials. Missing local evidence must surface as missing/synthetic provenance rather than being silently promoted.

Do not add broad `COPY . .`, mount `key/`, expose the research runner from this image, or enable live flags.

## Local development

Use the [local research stack runbook](../docs/runbooks/local-research-stack.md). It is the supported way to run API, runner, and frontend together on a Windows workstation.

## Scheduled L2 operations

Schedule definitions are allowlisted in `tools/l2_schedule_entrypoint.py` and registered through `scripts/register_schedules.ps1`. Registration is an operator action after a clean, verified release; source code landing does not automatically install or activate schedules.

See:

- [L2 research operations](../docs/runbooks/l2-research-operations.md)
- [Hourly alpha operations](../docs/runbooks/hourly-alpha-operations.md)
- [Backup and restore](../docs/runbooks/backup-restore-runbook.md)

## Testnet

Testnet adapters live under `live/` and are isolated from the deterministic services core. Testnet activity requires the venue-specific runbook, validation attestation, mandate, risk gates, reconciliation, audit evidence, and explicit human authorization.

- [MFT Testnet operations](../docs/runbooks/mft-testnet-operations.md)
- [Binance USDT continuous paper](../docs/runbooks/binance-usdt-continuous-paper.md)

Testnet connectivity does not make a blocked Alpha eligible.

## Release checklist

Before building or scheduling from a commit:

1. Worktree is clean and the intended commit is recorded.
2. `python scripts/check_repository_hygiene.py` passes.
3. `python scripts/validate.py` passes all stages.
4. Generated evidence binds to the canonical source commit.
5. Runtime flags remain L2-safe.
6. No secret or runtime database is in the image or Git diff.
7. Rollback and backup/restore evidence are current for the intended operation.

Container reproducibility and vulnerability evidence live under `evidence/reproducibility/` and `evidence/vulnerability/`. They are evidence about a specific source/input set, not permanent certification.
