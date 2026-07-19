# QUANTOS Restore Drill Design

## Goal

Close the `OPS.RESTORE.DRILL` evidence gap with an offline, repeatable restore drill that proves a backup batch can be recovered without touching active QUANTOS state.

## Safety Boundary

- Remains `L2_DRY_RUN_ONLY`.
- Never writes into `data/cache`, `key`, broker, credential, or live execution paths.
- Restores only into a caller-supplied empty directory or an internally-created temporary directory.
- Encrypted backups require `QUANTOS_BACKUP_PASSPHRASE`; plaintext backups require an explicit `--allow-plaintext` flag.
- Any ambiguity, collision, missing input, decrypt failure, integrity mismatch, or RTO breach fails closed.

## Design

Add `tools/restore_drill.py` with a pure orchestration function and a thin CLI.

The drill scans one timestamped backup batch. Each `.enc` file is decrypted through the existing `tools.live_backup.decrypt_file`; plaintext files are copied only after explicit opt-in. Basenames must be unique after removing `.enc`. Restored bytes are hashed with SHA-256 and compared to the source plaintext bytes or to a fresh decryption of the encrypted source. The active system is never mutated.

The result records batch path, mode, file count, restored filenames, elapsed milliseconds, configured RTO, integrity status, and overall verdict. The CLI writes this generic deterministic-schema evidence to `evidence/generic_restore_drill_latest.json` by default only after a successful drill. This generic artifact does not satisfy the L2 authority-state restore requirement; that requirement consumes `evidence/l2_backup_latest.json`. Wall-clock duration and run timestamp are boundary evidence, not inputs to trading or research decisions.

## Failure Semantics

- Missing or empty batch: fail.
- Mixed encrypted/plaintext batch: fail.
- Missing/weak passphrase for encrypted batch: fail.
- Plaintext batch without explicit opt-in: fail.
- Existing non-empty restore target: fail.
- Duplicate restored names: fail.
- Decryption or byte-integrity mismatch: fail.
- Elapsed duration above `--rto-ms`: fail and do not publish PASS evidence.

## Verification

- Encrypted round-trip succeeds in a temporary directory.
- Wrong password fails closed.
- Plaintext requires explicit opt-in.
- Mixed batches and non-empty targets fail closed.
- Evidence schema records RPO/RTO-relevant fields without secrets.
- Governance test prevents network, broker, order, or credential-path behavior.
