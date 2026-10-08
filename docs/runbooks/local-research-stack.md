# Local research stack runbook

Status: **CURRENT**  
Scope: local L2 research console only. This runbook does not start exchange adapters, live schedulers, promotion services, or order submission.

## Components

| Component | Default port | Capability |
|---|---:|---|
| Read-only console API | 8400 | GET-only research, evidence, risk, and operations views |
| Guarded research runner | 8420 | Whitelisted research jobs; no broker/order/credential path |
| React console | 3100, or 3110 when reserved | Operator UI |

## Managed Windows startup

From the repository root in PowerShell:

```powershell
./scripts/start_console.ps1
```

The launcher delegates to `tools.local_console_stack`, creates no visible child consoles, writes its manifest/logs under `.runtime/console-stack/`, chooses an available frontend port, and waits for health checks.

Inspect status:

```bash
py -3.14 -m tools.local_console_stack status
```

Stop only the processes recorded in the managed manifest:

```powershell
./scripts/stop_console.ps1
```

Do not stop processes by broad name matching.

## Manual startup

Use three terminals from the same checkout.

Read-only API:

```bash
python -m uvicorn services.console.main:app --host 127.0.0.1 --port 8400
```

Guarded research runner:

```bash
python -m uvicorn services.research_runner.main:app --host 127.0.0.1 --port 8420
```

Frontend:

```bash
cd apps/research-console
VITE_API_URL=http://127.0.0.1:8400 \
VITE_RUNNER_URL=http://127.0.0.1:8420 \
VITE_MOCK_MODE=false \
npm run dev -- --host 127.0.0.1 --port 3100
```

The research runner may require a machine-local token. Keep it in the ignored local key store and enter it only through the UI/runtime mechanism; never print or commit it.

## Separately frozen research workspaces

The factory and lifecycle pages can display separately frozen research without
moving their state or changing the source version used by an existing worker.
Configure only the **console** process with an absolute
`QUANTOS_RESEARCH_WORKSPACES_PATH` pointing to an operator-owned JSON file:

```json
{
  "version": 1,
  "workspaces": [
    {"id": "frozen-study", "label": "Frozen observation", "root": "/absolute/research/state"}
  ]
}
```

Each root must contain `data/cache/ai_factory/factory.db` and be readable by the
console service account. At most 16 entries are accepted. IDs must be unique;
`current` is reserved for `QUANTOS_STATE_ROOT`. Filesystem paths are never accepted
from HTTP query parameters or returned in the workspace catalogue. Invalid
configuration and unavailable registered roots remain explicit errors.

Open `/studio/factory?workspace=frozen-study&attempt=4` or select the research
space on the factory/lifecycle page. All additional spaces are read-only. The
selector clears task IDs when changing spaces, links retain the selected space,
and the Runner rejects mutations carrying any workspace other than `current`.
The existing token and research action checks still apply to current-space writes.
Old servers that ignore workspace selection are rejected by the new frontend;
deploy the console, Runner and frontend as a compatible set.

A read-only view does not prove that a worker is alive: use the source record's
observation time and reported progress. Viewing does not schedule, resume, copy,
promote or rewrite the original study. Keep frozen workers on their original
source. In particular, upgrading an interface is not permission to resume an old
experiment with a different engine. A system service with `ProtectHome=true`
cannot read developer studies under `/home`; this setting does not change that
isolation. On DGX, the existing separate research interface is the appropriate
place to configure these views after release acceptance.

## Health checks

```bash
curl http://127.0.0.1:8400/health
curl http://127.0.0.1:3100/research
```

Expected API mode is `READ_ONLY`. A healthy API does not imply paper readiness, candidate admission, Testnet authorization, or Mainnet authority.

## Troubleshooting

- If port 3100 is reserved on Windows, use the managed launcher or select another localhost port.
- On this workstation, a duplicate `Path`/`PATH` environment view can break PowerShell process enumeration and `Start-Process`. The managed launcher avoids that path.
- If the UI reports missing artifacts, verify the intended state root and evidence freshness; do not manufacture placeholder PASS data.
- If a managed service is stale, stop it with the manifest-aware command, then start again.
- Never solve a startup issue by enabling live flags or loading exchange credentials.

## Before sharing results

Run:

```bash
py -3.14 scripts/validate.py
```

Report the exact evidence state, including blockers. “Console started” and “strategy approved” are separate facts.
