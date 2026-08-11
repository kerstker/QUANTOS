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
