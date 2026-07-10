# Unified Validation Gate Design

## Goal

Close `PLATFORM.CI.UNIFIED_GATE` by making `python scripts/validate.py` the single fail-closed command for governed evidence, backend tests, live-stack tests, and the production frontend.

## Design

Resolve the platform npm executable with `shutil.which` so Windows uses `npm.cmd` without a shell. Add three ordered checks in `apps/research-console`: `npm run typecheck`, `npm test`, and `npm run build`. Each remains a separate subprocess and any non-zero exit keeps the overall gate red.

The top-tier registry will require the exact frontend command markers plus existing governance/live markers before awarding M4. No network dependency audit is claimed by this requirement; supply-chain evidence remains a separate red line.

## Verification

- Unit test asserts all frontend commands are present in the canonical gate.
- Existing frontend suite and build run through the gate.
- Full `scripts/validate.py` must pass before commit.

