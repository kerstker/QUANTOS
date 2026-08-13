# Linux systemd templates

Status: **DISABLED INPUT TEMPLATES — L2_DRY_RUN_ONLY**

These files are the Linux/ARM64 replacement for the Windows Task Scheduler
boundary. They preserve the split between an immutable release under
`/opt/quantos/releases/<40-hex-commit>` and mutable authority state under
`/srv/quantos/state`.

The checked-in units contain the literal `__QUANTOS_RELEASE_SHA__` marker. They are not
deployable unit files and must never be copied directly into a system unit
directory. A governed renderer must bind an exact clean commit, an allowlisted
module, its explicit cadence and timeout, then run offline unit verification.
Rendering does not install, enable, or start anything.
The renderer has separate modes for allowlisted one-shot workers and for the
resident stream/watchdog trio; both modes bind the same immutable commit token.
Rendered worker calendars pin `Asia/Shanghai` explicitly, preserving the
reviewed Windows wall-clock schedule even if the host timezone later drifts.

All services run as the dedicated unprivileged `quantos` account with a strict
filesystem boundary. The five non-secret L2 safety flags are literal properties
of every service. No `EnvironmentFile=` is permitted because systemd environment
files override matching `Environment=` values regardless of textual order.
Backup passphrases must be provided only to the backup unit through a systemd
credential and the file-based backup interface; they must not be placed in an
environment variable or on a command line.

On Linux, a backup or seal destination is accepted only when its current
device, mount identity, durable filesystem type, and distinct source can be
proved from `/proc/self/mountinfo`. A separate filesystem is an isolation
boundary, not by itself proof of a separate physical disk or off-host copy;
operators must retain the independent disaster-recovery copy required by the
deployment policy.

The resident Binance stream is public-market-data only. It is isolated in its
own service so restart and process-tree shutdown semantics do not weaken the
short-lived worker template. No checked-in unit has broker credential access,
Mainnet authority, order submission authority, or permission to write inside
the immutable release.
