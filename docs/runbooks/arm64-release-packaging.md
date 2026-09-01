# Native ARM64 release packaging and cutover contract

This runbook replaces hand-copied release hashes and worker lists with the
unprivileged package builder in `tools/build_arm64_release_package.py`.  It does
not weaken the runtime boundary: the target remains `L2_DRY_RUN_ONLY`; Mainnet,
real brokers, order submission, AI direct trading, and every
`INVALIDATED_PERP_LEDGER_V1` entrypoint remain unavailable.

## 1. What the builder produces

One package contains:

- a Git bundle for one clean, immutable 40-character release commit;
- the already-tested frontend `dist/` tree;
- rendered native Linux/ARM64 units for the previously deployed public-data
  streams, backup/watchdog and worker timers;
- `quantos-research-runner.service`, loopback-only on port 8420;
- the two admitted A-share timers:
  `ashare_alpha_search_worker` and `ashare_forward_observation`;
- hashes and semantic counts for an external TongDaXin SQLite/panel pair; and
- an exact `SHA256SUMS` plus `PACKAGE_MANIFEST.json` cutover contract; and
- an executable, package-hash-bound `deploy.sh` implementing the transaction
  below (no hand-copied release hashes or worker arrays).

The package deliberately does not copy the large TDX files or scan their
directory.  Only the two explicitly supplied final files are bound.  Recovery
copies such as `.pre-v2-*.bak`, partial panel files and intake scratch files can
therefore never become canonical package payloads accidentally.

The builder has no `sudo`, `systemctl`, install, enable, start, credential or
order capability.  It fails if the Git tree is dirty, the release evidence is
not bound to HEAD, the frontend contains a symlink, the TDX database has a bad
integrity/PIT contract, the panel is not bound to that exact database SHA, or
the Python dependency lock differs from the currently installed release.

## 2. Build after validation and the immutable release commit

Run the full backend and frontend gates first.  Generate the canonical release
evidence, commit it, and confirm that the worktree is completely clean.  Then:

```bash
release=$(git rev-parse HEAD)
python -m tools.build_arm64_release_package build \
  --release-commit "$release" \
  --frontend-dist apps/research-console/dist \
  --tdx-sqlite /home/kerstker/quantos-data-intake/ashare-pit/staging/tdx-windows-20260830/ashare_tdx_pit.sqlite \
  --tdx-panel /home/kerstker/quantos-data-intake/ashare-pit/staging/tdx-windows-20260830/ashare_tdx_pit.adv20000000.panel.npz \
  --current-release-root /opt/quantos/releases/55cdeb7ec11ad7ab1a987309a24b473d8184fb9a \
  --backup-mount /mnt/quantos-backup \
  --output "/home/kerstker/quantos-migration-20260813/staging/dgx-arm64-${release:0:8}"

python -m tools.build_arm64_release_package verify \
  "/home/kerstker/quantos-migration-20260813/staging/dgx-arm64-${release:0:8}"

sudo -- "/home/kerstker/quantos-migration-20260813/staging/dgx-arm64-${release:0:8}/deploy.sh"
```

The TDX panel must be the final stable panel generated after the v2 SQLite
upgrade.  Do not package a concurrently written `.tmp.npz` or the v1 recovery
database.

## 3. Privileged installer: required transaction, not optional gates

The successful installer
`dgx-binance-audit-renewal-arm64-55cdeb7e/deploy.sh` is the audited transaction
baseline.  A new installer may be shorter, but it must preserve the following
behavior.  These stages prevent the historical watchdog/backup deployment
failures; they are not profitability or promotion gates.

1. Verify `uname -m == aarch64`, the exact package hashes, bundle identity,
   release evidence, state ownership and unchanged Python dependency lock.
2. Install the Git checkout, venv link and frontend under commit-addressed
   `/opt/quantos` paths.  Never run an x86 container or emulation layer.
3. Render the package's 18 worker pairs, four resident/watchdog units and the
   runner.  Bind every executable and `PYTHONPATH` to the same release commit,
   then run `systemd-analyze verify` before touching installed units.
4. Stop only worker and watchdog timers.  Let active oneshot workers finish;
   never terminate a running backup or data worker to make deployment faster.
   Do not carry forward the baseline script's
   `recover_preexisting_worker_failures` loop: it can synchronously rerun
   Wikimedia or another optional public refresh for 15 minutes.  Unit
   replacement followed by `reset-failed` is sufficient; the timer owns its
   next normal retry.
5. Stop the console and resident services, snapshot every unit that already
   exists, and mark the new runner as a new-only unit so rollback removes it if
   there was no old runner unit.
6. Publish the pre-verified TDX pair while all possible readers are quiescent,
   using same-directory incoming names and same-filesystem renames.  Preserve
   both old files in the deployment rollback directory.  If either rename or
   post-publish check fails, restore both old files before services restart.
7. Install the new units, reload systemd, and restart the proxy importer,
   Binance/Bybit public streams, console API, frontend, and research runner.
8. Require fresh semantic receipts from both resident streams; require HTTP
   200 from ports 8400, 3100 and runner `/health` on 8420.  Runner health must
   list the A-share technical search/forward kinds and must not list a legacy
   V1 kind as runnable.
9. Re-enable the 18 worker timers and watchdog timer.  A newly admitted timer
   has no prior `NextRun`/execution history, so do **not** probe watchdog yet.
   Start `ashare_alpha_search_worker` with `--no-block`, confirm its service is
   active/activating, and then prove that it actually owns
   `data/cache/ashare_alpha_runtime.lock` (a non-blocking `flock` must fail).
   `ActiveState=activating` alone is racy: forward could otherwise acquire the
   lock first and fail before a freeze exists.  Only after lock ownership is
   proven, start `ashare_forward_observation`; its expected busy result is
   status 2 with systemd `Result=success`, which safely creates the first timer
   history without reading a partial search result.
10. Probe watchdog once.  Exit status 2 is accepted only when systemd records `Result=success`
   (because the reviewed unit declares `SuccessExitStatus=2`); an `exit-code`,
   timeout or signal result is a deployment failure even if the numeric status
   happens to be 2.
11. Require every timer active, both streams active, all three official console
    services active, no failed `quantos-*` unit, and exact release bindings.
12. Leave the scheduled backup and Wikimedia refresh enabled, but do not run
    either synchronously in the deployment critical path.  They are independent
    scheduled jobs, not prerequisites for bringing research/console online.
13. End the rollback-sensitive cutover transaction while the search continues,
    then monitor that already-running search with 16-second heartbeats for at
    most its reviewed two-hour systemd timeout.  After it records
    `Result=success`, run forward observation again (status 2 remains a valid
    `SAFE_BLOCKED` result when no new post-cutoff session exists), and probe
    watchdog a second time.  The long wait never keeps unit/data rollback open.
14. Only after the final 8400/3100/8420, search/forward and watchdog checks pass,
    stop the exact three obsolete user transient units.  Do not use `pkill`, a
    process-name wildcard or any broader cleanup.

### TDX v2 pair publication

The prior installer copied TDX files during preflight and refused any installed
hash change.  That logic cannot deploy v2.  Replace it with this exact state
machine:

```text
SOURCE_VERIFIED
  -> BOTH_INCOMING_FILES_COPIED_AND_HASHED
  -> SQLITE_QUICK_CHECK_AND_PANEL_DB_SHA_PASS
  -> READERS_QUIESCED
  -> BOTH_OLD_FILES_MOVED_TO_ROLLBACK
  -> SQLITE_RENAMED
  -> PANEL_RENAMED
  -> POST_PUBLISH_PAIR_VERIFIED
```

Any failure after `READERS_QUIESCED` restores both prior files.  The installer
must not delete the rollback pair during the same transaction.  There is no
claim of cross-file atomic rename; quiescing readers is what makes the two
ordered same-filesystem renames safe.

The same rollback snapshot binds exactly four A-share runtime artifacts that
the first search/forward probe may change: `ashare_alpha_search.json`,
`ashare_forward_freeze.json`, `ashare_forward_observation.db`, and
`ashare_forward_observation.json`.  If the infrastructure transaction fails,
the installer first stops the runner, waits for the systemd worker to finish,
then restores (or removes, when originally absent) those four files before
restoring the old TDX pair.  No other cache file is touched.

## 4. Exact old transient cleanup

Do this only after the official 8400/3100/8420 chain and watchdog probe pass.
The commands stop exactly the obsolete user-level MVP units; they do not kill
arbitrary Python processes and do not delete their state:

```bash
sudo -- runuser -u kerstker -- env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user stop \
    quantos-mvp-console-api.service \
    quantos-mvp-research-runner.service \
    quantos-mvp-frontend.service

sudo -- runuser -u kerstker -- env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user is-active \
    quantos-mvp-console-api.service \
    quantos-mvp-research-runner.service \
    quantos-mvp-frontend.service
```

`is-active` should print `inactive` for all three and returns nonzero in that
expected state.  If `/run/user/1000/bus` does not exist, verify the units are
not running with `systemctl --user` from the logged-in `kerstker` session; do
not broaden the cleanup to `pkill`, wildcards or process-name matching.

## 5. Deliberate exclusions

- No old A-share research driver, V1 paper-fill, legacy multi-period ledger or
  quarantined crypto discovery entrypoint is installed or scheduled.
- No Mainnet/testnet credential is read or changed by packaging/deployment.
  Testnet execution remains a separate credential/mandate-gated workflow.
- No synchronous Wikimedia refresh or backup run occurs during cutover.
- No promotion, paper persistence or order submission is implied by a technical
  A-share search result or forward observation.
