# End-to-End Pipeline Integration — Design (sub-project 1 of N)

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit/roadmap driver: the subsystems (persisted data lake, alpha evaluator/factory,
guided search, experiment registry) are built but SILOED — the missing seam is
"persisted bars → PanelPeriods", so nothing actually runs the alpha stack on the
real persisted data substrate.

## 0. Decomposition

Full integration = (a) the data→PanelPeriod seam, (b) a reproducible orchestrator,
(c) consolidating the two divergent promotion paths (validation/pipeline.py vs
alpha/evaluator). This spec builds **sub-project 1: the data→PanelPeriod seam + a
built-in signal-operator library + a persisted-data periods-provider** so guided
search / the factory run on real persisted bars. The orchestrator and the
pipeline.py consolidation are later sub-projects (the latter is a riskier refactor
with its own tests — deferred deliberately).

## 1. Goal

Close the missing seam: turn persisted PIT bars (`SqlitePITStore` / any
`PITStore`) into `PanelPeriod` objects (cross-sectional signal + realized forward
return) PIT-correctly, with a small deterministic signal-operator library keyed by
`AlphaDefinition.params{operator, window}`, and a `periods_provider` that lets
`GuidedSearchLoop.search(...)` / `AlphaFactory.run(...)` run on the real data
substrate instead of hand-built synthetic periods.

Constraints: L2_DRY_RUN_ONLY; pure-Python stdlib; deterministic; fail-closed/
fail-soft; no lookahead (signals use only data knowable by t via `store.as_of`);
reuse `PanelPeriod`, the persisted store, and the guided-search loop unchanged.

## 2. Architecture

New modules in `services/research/`:
```
signals.py        SIGNAL_OPS registry: operator(closes, window) -> Optional[float]
panel_builder.py  build_panel_periods(...) ; make_data_periods_provider(...)
```
Reuse unchanged: `services.alpha.panel_backtest.PanelPeriod`; `SqlitePITStore`/
`PITStore` (`as_of`/`latest_as_of`); `GuidedSearchLoop`/`AlphaFactory`/`AlphaEvaluator`.
Imports: contracts + services.data + panel_backtest — no live surface.

## 3. Components

### 3.1 `signals.py`
- `SIGNAL_OPS: dict[str, Callable[[Sequence[float], int], Optional[float]]]` — each
  takes the instrument's PIT close history (oldest→newest) + a window, returns a
  per-instrument scalar signal or `None` if the history is too short:
  - `"zscore"`: `(closes[-1] - mean(last window)) / pstdev(last window)` (None if <window or std 0).
  - `"momentum"`: `closes[-1]/closes[-window] - 1` (None if <window or closes[-window]==0).
  - `"ts_mean"`: `closes[-1]/mean(last window) - 1` (None if <window or mean 0).
- Deterministic, pure. Unknown operator → `KeyError` at lookup (fail-closed).

### 3.2 `panel_builder.py`
- `build_panel_periods(store, instruments, *, event_times, fwd_horizon, signal_fn) ->
  list[PanelPeriod]`:
  for each `t = event_times[i]` with `i + fwd_horizon < len(event_times)`:
  cross-section over `instruments` — signal = `signal_fn([b.close for b in
  store.as_of(inst, t)])` (PIT: only bars knowable by t); current close =
  `store.latest_as_of(inst, t).close`; forward close =
  `store.latest_as_of(inst, event_times[i+fwd_horizon]).close`; forward_return =
  `fwd/now - 1`. Instruments with `None` signal / missing close are SKIPPED
  (fail-soft). A period is emitted only when ≥2 instruments survive (the long/short
  panel needs both legs). Deterministic (instruments + event_times sorted).
- `make_data_periods_provider(store, instruments, *, event_times, fwd_horizon) ->
  Callable[[AlphaDefinition], list[PanelPeriod]]`: returns a provider that reads
  `defn.params["operator"]`/`["window"]`, builds `signal_fn = lambda closes:
  SIGNAL_OPS[op](closes, window)`, and calls `build_panel_periods`. This is the
  object passed to `GuidedSearchLoop.search(periods_provider=...)`.

## 4. Data flow

```
SqlitePITStore (persisted bars) ──► build_panel_periods (PIT signal + realized fwd return)
                                          ▲ signal_fn from defn.params via SIGNAL_OPS
GuidedSearchLoop.search(periods_provider = make_data_periods_provider(store, ...))
   └─► proposes hypotheses ─► evaluates on REAL persisted-data periods ─► learns
```

## 5. Error handling

- No lookahead: the signal uses ONLY `store.as_of(inst, t)` (knowledge_time ≤ t);
  the forward return is the realized outcome (legitimately uses a later close, never
  fed into the signal).
- Fail-soft: instruments lacking enough history / a forward close are dropped from
  that cross-section; a period with <2 survivors is not emitted (no degenerate
  single-leg panel).
- Unknown operator → `KeyError` (fail-closed); non-finite closes handled by the
  operators returning None.
- Determinism: sorted instruments/event_times; no RNG; identical store + params →
  identical periods.

## 6. Testing (tests-first)

- `tests/research/test_signals.py`: each operator's formula + None on short history
  + None on degenerate (zero std / zero base); unknown op KeyError.
- `tests/research/test_panel_builder.py`: build from a SqlitePITStore loaded with a
  few instruments over several event_times — signals are PIT (a bar knowable only
  later does not affect an earlier period); forward returns are realized; an
  instrument with insufficient history is skipped; periods well-formed (equal-length
  tuples, ≥2 instruments); determinism.
- `tests/research/test_pipeline_integration.py`: load bars into a SqlitePITStore →
  `make_data_periods_provider` → `GuidedSearchLoop(space).search(...)` runs on the
  real data path, produces evaluations, NOTHING reaches APPROVED, and is
  deterministic across two runs.

## 7. Out of scope (later sub-projects)

A single reproducible `run_research_session` orchestrator (load registry → search →
persist) threading dataset_version end-to-end; consolidating/deprecating
`validation/pipeline.py` (single-instrument path) into the canonical
evaluator/factory path; universe-driven instrument selection; multi-factor signals.
Sub-project 1 delivers the data→PanelPeriod seam + operator library + provider only.
