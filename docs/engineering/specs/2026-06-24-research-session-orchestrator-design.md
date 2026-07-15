# Reproducible Research Session Orchestrator — Design (#2 remainder)

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Roadmap: #2 remainder — one reproducible call that composes the now-integrated
subsystems (persisted data → PanelPeriods → guided search → registry persistence).

## 1. Goal

A single deterministic `run_research_session(...)` that:
1. derives a `dataset_version` from the persisted bars' content hash (reproducible,
   auto-versioned as data changes),
2. (optionally) `load_into`s a catalog from an `ExperimentRegistry` (cross-session
   multiple-testing trial count + var_sr),
3. runs the guided search over a `HypothesisSpace` on the REAL persisted-data
   PanelPeriods (the unchanged guardrailed evaluate+advance path — agent still can
   never reach APPROVED),
4. records each resulting `AlphaEvaluation` as an immutable registry artifact keyed
   by `dataset_version` (with an optional audit event), and
5. `save`s the evolved catalog back to the registry.

Composes existing entry points; changes none of them. Pure-Python, deterministic,
fail-closed, no new authority, containment + import-boundary preserved.

## 2. Architecture

New module `services/research/session.py`. Reuses unchanged: `SqlitePITStore`,
`make_data_periods_provider`, `HypothesisSpace`/`GuidedSearchLoop`/`ResearchAgentLoop`,
`AlphaCatalog`, `ExperimentRegistry`, `versioning.content_hash_of`.

`pipeline.py` is NOT consolidated here (it is a different-granularity SINGLE-instrument
validation tool, not a divergent duplicate of the cross-sectional evaluator path);
this is documented, not refactored (a removal is risky and out of scope).

## 3. Component

`run_research_session(store, instruments, *, event_times, fwd_horizon, space, rounds,
batch_size, timestamp=0, registry=None, dataset_version=None, audit_log=None) -> dict`:
- `dataset_version`: if None, `content_hash_of([bar for inst in instruments for bar in
  store.fetch_bars(inst, min(event_times), max(event_times))])`. Empty store -> "".
- catalog: `AlphaCatalog()`; if `registry`, `registry.load_into(catalog)` first
  (propagates `ExperimentStoreError` — fail-closed; never silently empties trials).
- `loop = GuidedSearchLoop(space, inner_loop=ResearchAgentLoop(catalog=catalog))`.
- `provider = make_data_periods_provider(store, instruments, event_times=event_times,
  fwd_horizon=fwd_horizon)`.
- `out = loop.search(rounds, batch_size, provider, timestamp=timestamp)`.
- for each `alpha_id, entry` in `out["report"]` with an `"evaluation"`: if `registry`,
  `run_id = registry.record_evaluation(ev, dataset_version=dataset_version,
  recorded_ts=timestamp, audit_log=audit_log)` (collect run_ids). Skip errored
  candidates (no "evaluation"). A re-record of identical content is an idempotent
  no-op (registry ER-1 fix).
- if `registry`: `registry.save(catalog, now=timestamp)`.
- return `{dataset_version, arm_means, human_review_queue, recorded_run_ids,
  n_evaluated, report}`.

## 4. Error handling

- Fail-closed: registry load/save errors propagate (`ExperimentStoreError`).
- Containment: the search drives the unchanged `ResearchAgentLoop` (non-human AGENT)
  → no APPROVED; re-tested at the session level.
- Determinism: no RNG anywhere in the composed path; identical store + space + params
  -> identical `dataset_version`, `arm_means`, and `recorded_run_ids`.

## 5. Testing (tests-first) — `tests/research/test_session.py`

- end-to-end on a persisted store + registry: returns a non-empty `dataset_version`,
  records evaluations (run_ids), nothing reaches APPROVED.
- cross-session persistence: after one session the registry holds the catalog trial
  population; a second session's `load_into` sees the accumulated trial count
  (`global_trial_count` does not reset).
- determinism: two sessions (fresh registries) give identical `dataset_version` and
  `arm_means`.
- works WITHOUT a registry (no persistence) — still runs + returns a report.
- import boundary: session.py imports only contracts + services (no live surface).

## 6. Out of scope

pipeline.py removal/merge (documented as a distinct tool); threading dataset_version
INTO the evaluator's reproducibility_hash (the agent path does not pass it — the
registry run_id already incorporates dataset_version); a CLI; scheduling.
