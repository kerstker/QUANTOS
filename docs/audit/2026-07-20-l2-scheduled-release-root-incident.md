# L2 scheduled release-root incident review — 2026-07-20

## Incident

`QUANTOS_domain_event_ops` started at 15:41 and again at 16:41 Asia/Shanghai.
Each process consumed approximately one CPU core continuously, used roughly
100–114 MB of memory, and did not finish before manual inspection. Both were
terminated, and the single affected schedule was disabled while investigating.
No broker, order, credential, or live-trading path was involved.

## Root cause

The Windows action was `python -m tools.domain_event_ops` with
`H:\code\QUANTOS` as its working directory. That mutable operational checkout
was still on an old code tree and also contained extensive uncommitted work.
Its repository had `core.bare=true`, which hid the dirty worktree from normal
status/switch operations. The old event-bus implementation replayed all 61,472
event records and 99,621 consumer-journal records on every heartbeat/claim/ack,
so the hourly worker repeatedly consumed the full authority instead of using
the verified-frontier freshness sentinel already present in the current
release.

The operational files were not stashed, reset, moved, or overwritten. Only the
invalid local `core.bare` flag was restored to `false`, exposing the pre-existing
changes honestly.

## Containment and recovery evidence

- The faulty task remained disabled during repair.
- Current release code ran against the same authority and exited in 14–22
  seconds per bounded 1,000-event batch.
- The old blockage had left 10,669 events pending. Eleven foreground bounded
  runs reduced pending events to zero.
- Every batch verified the event, consumer, and coordination chains; all three
  remained valid. The final event-health verdict was `PASS` with zero pending
  events and zero dead letters.
- The overall operations verdict remained `PARTIAL`, not falsely `PASS`, because
  optional L2 kill-switch and deployment owner stores are absent in this state
  root.

## Permanent remediation

Scheduled code and mutable state are now separate contracts:

1. `tools/l2_schedule_entrypoint.py` derives its code root from its own absolute
   release path, permits only the fixed L2 module allowlist, verifies the loaded
   worker came from that release, and leaves the state working directory intact.
2. `scripts/register_schedules.ps1 -StateRoot ...` registers absolute launcher
   actions from the validated release while retaining the existing operational
   `data/cache` root.
3. The scheduler watchdog validates launcher path, module, state root, and
   Python executable; old direct `-m tools.*` actions are rejected.
4. Domain-event operations are overlap-protected and capped at five minutes.

The task may be re-enabled only after the new registration is installed from a
clean release, the real action exits automatically, the watchdog accepts its
metadata, and no related process remains.
