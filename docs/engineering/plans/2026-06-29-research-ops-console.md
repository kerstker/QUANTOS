# Research Operations Console — Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the read-only frontend into an operations console — a dynamic, registry-backed area that auto-reflects every backtest/research run, plus a guarded `research_runner` service so runs are triggered from the frontend (never the IDE), all within the L2 read-only safety model.

**Architecture:** A single source of truth `run_registry` (stdlib sqlite). Two links read/write it: the read-only `console_api` (GET endpoints) the frontend polls for viewing, and a separate guarded `research_runner` (POST trigger → async job → registry) that is the ONLY mutating surface and is machine-pinned to research-only kinds. Frontend gets one dynamic "Research Runs" section + one generic data-driven viewer (no per-backtest page) + trigger buttons.

**Tech Stack:** Python 3.14 stdlib only in `services/` (sqlite3, json, hashlib, concurrent.futures); FastAPI for the two services (same pattern as existing `console_api`); React 18 + Vite 6 + TS strict + Tailwind for the console.

## Global Constraints

- Pure-Python **stdlib only** in `services/` (no numpy/pandas/ccxt/broker SDK). Deterministic: **no RNG, no wall-clock** in `services/` core — timestamps are injected by callers; the runner may take wall-clock at its `tools/`-style boundary only.
- Safety (non-negotiable, machine-enforced): `LIVE_TRADING_ENABLED=false`; `auto_approved` stays **0**; the runner NEVER imports `services.trading`/broker SDKs and NEVER emits order/transfer calls (`create_order`/`place_order`/`submit_order`/`.withdraw(`/`fapiPrivate`/…); the runner reaches no lifecycle APPROVED / live / flag surface; `console_api` stays **GET-only**.
- Single gate: `python scripts/validate.py` must stay green (governance scanner + all suites). Frontend gate: `npm run typecheck && npm test && npm run build` in `apps/research-console`.
- Commits: Chinese message, end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; after each commit `git branch -f main master`.
- Run python via `rtk proxy python ...`; rg/gh not on rtk PATH (use Grep tool / full paths).

---

## File Structure

- `services/research/run_registry/__init__.py` — package
- `services/research/run_registry/contract.py` — `ResearchRun` dataclass + validation
- `services/research/run_registry/store.py` — `ResearchRunStore` (sqlite3 CRUD)
- `services/research/run_registry/registry.py` — `RunRegistry` (validated facade)
- `services/research/run_registry/indexer.py` — `index_existing(cache_dir, registry, *, now)` + per-kind adapters
- `services/research/research_runner/__init__.py` — package
- `services/research/research_runner/run_kinds.py` — `RUN_KINDS` whitelist (kind → entrypoint + param schema + adapter)
- `services/research/research_runner/runner.py` — `JobQueue` + `submit(kind, params, now)`
- `services/research/research_runner/main.py` — FastAPI app (`POST /run`, `GET /health`)
- `services/research/console_api/main.py` — MODIFY: add 2 GET endpoints reading the registry
- `tests/research/test_run_registry.py`, `tests/research/test_run_indexer.py`, `tests/research/test_research_runner.py`, `tests/governance/test_research_runner_boundary.py`, MODIFY `tests/research/test_console_api_contract.py` + `services/research/console_api/tests/test_console_api.py`
- `apps/research-console/src/pages/ResearchRuns.tsx`, `RunDetail.tsx`; `src/lib/api/` (read client + runner client); `src/lib/types/research.ts` (MODIFY); `src/App.tsx` (MODIFY nav+routes); smoke test (MODIFY)

---

### Task 1: run_registry — contract + store + registry

**Files:**
- Create: `services/research/run_registry/__init__.py`, `contract.py`, `store.py`, `registry.py`
- Test: `tests/research/test_run_registry.py`

**Interfaces:**
- Produces: `ResearchRun` (frozen dataclass, fields per spec §3); `ResearchRunStore(db_path, create=True)` with `upsert(run)`, `get(run_id)->ResearchRun|None`, `list(kind=None, limit=200)->list[ResearchRun]` (created_ts desc), `set_status(run_id, status, error=None)`; `RunRegistry(store)` with same methods + validation; `make_run_id(kind, params, created_ts)->str`; `VALID_STATUS={'queued','running','done','error'}`.

- [ ] **Step 1: Write failing tests** — `tests/research/test_run_registry.py`:

```python
import pytest
from services.research.run_registry.contract import ResearchRun, make_run_id, VALID_STATUS
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry

def _run(**kw):
    base = dict(run_id="r1", kind="carry_strategy", title="t", params={"q": 0.3}, status="done",
                created_ts=1000, finished_ts=1010, metrics={"net_sharpe": 2.03},
                series={"per_year": [4.4, 2.8]}, verdict="DEPLOYABLE", caveats=["survivorship"],
                provenance="tool:carry", error=None)
    base.update(kw); return ResearchRun(**base)

def test_run_id_is_deterministic():
    a = make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a == make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a != make_run_id("carry_strategy", {"q": 0.3}, 1001)

def test_store_round_trip(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run())
    got = s.get("r1")
    assert got.kind == "carry_strategy" and got.metrics["net_sharpe"] == 2.03
    assert got.series["per_year"] == [4.4, 2.8]

def test_list_is_newest_first_and_filterable(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="a", kind="carry_strategy", created_ts=1))
    s.upsert(_run(run_id="b", kind="oi_research", created_ts=2))
    assert [r.run_id for r in s.list()] == ["b", "a"]
    assert [r.run_id for r in s.list(kind="oi_research")] == ["b"]

def test_set_status(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(status="queued", run_id="q1"))
    s.set_status("q1", "running"); assert s.get("q1").status == "running"
    s.set_status("q1", "error", error="boom"); assert s.get("q1").error == "boom"

def test_registry_rejects_bad_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(status="bogus"))

def test_registry_rejects_unknown_kind(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(kind="place_order"))   # only whitelisted research kinds allowed
```

- [ ] **Step 2: Run, verify fail** — `rtk proxy python -m pytest tests/research/test_run_registry.py -q` → FAIL (imports missing).

- [ ] **Step 3: Implement `contract.py`:**

```python
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field, asdict
from typing import Optional

VALID_STATUS = {"queued", "running", "done", "error"}
# kept in sync with research_runner.run_kinds.RUN_KINDS (Task 5); registry validates against this set
ALLOWED_KINDS = {"carry_strategy", "oi_research", "combo_book", "survivorship",
                 "factory_orthogonal", "factory_loop", "data_refresh"}

def make_run_id(kind: str, params: dict, created_ts: int) -> str:
    blob = kind + "|" + json.dumps(params, sort_keys=True, separators=(",", ":")) + "|" + str(created_ts)
    return "run_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    kind: str
    title: str
    params: dict
    status: str
    created_ts: int
    finished_ts: Optional[int]
    metrics: dict
    series: dict
    verdict: str
    caveats: list
    provenance: str
    error: Optional[str] = None
    def to_dict(self) -> dict: return asdict(self)
```

- [ ] **Step 4: Implement `store.py`** (sqlite3; one row per run, JSON blob for nested fields):

```python
from __future__ import annotations
import json, sqlite3
from typing import Optional
from .contract import ResearchRun

_COLS = ("run_id", "kind", "title", "status", "created_ts", "finished_ts", "verdict", "provenance", "error", "payload_json")

class ResearchRunStore:
    def __init__(self, db_path: str, create: bool = True):
        self._c = sqlite3.connect(db_path)
        if create:
            self._c.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT, title TEXT,"
                            " status TEXT, created_ts INTEGER, finished_ts INTEGER, verdict TEXT,"
                            " provenance TEXT, error TEXT, payload_json TEXT)")
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_ts)")
            self._c.commit()

    def upsert(self, r: ResearchRun) -> None:
        payload = json.dumps({"params": r.params, "metrics": r.metrics, "series": r.series, "caveats": r.caveats})
        self._c.execute("INSERT OR REPLACE INTO runs (run_id,kind,title,status,created_ts,finished_ts,verdict,"
                        "provenance,error,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r.run_id, r.kind, r.title, r.status, r.created_ts, r.finished_ts, r.verdict,
                         r.provenance, r.error, payload))
        self._c.commit()

    def _row(self, row) -> ResearchRun:
        p = json.loads(row[9])
        return ResearchRun(run_id=row[0], kind=row[1], title=row[2], status=row[3], created_ts=row[4],
                           finished_ts=row[5], verdict=row[6], provenance=row[7], error=row[8],
                           params=p["params"], metrics=p["metrics"], series=p["series"], caveats=p["caveats"])

    def get(self, run_id: str) -> Optional[ResearchRun]:
        r = self._c.execute(f"SELECT {','.join(_COLS)} FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, kind: Optional[str] = None, limit: int = 200) -> list:
        q = f"SELECT {','.join(_COLS)} FROM runs"
        args: tuple = ()
        if kind:
            q += " WHERE kind=?"; args = (kind,)
        q += " ORDER BY created_ts DESC LIMIT ?"
        return [self._row(r) for r in self._c.execute(q, args + (limit,)).fetchall()]

    def set_status(self, run_id: str, status: str, error: Optional[str] = None) -> None:
        self._c.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", (status, error, run_id))
        self._c.commit()
```

- [ ] **Step 5: Implement `registry.py`** (validated facade):

```python
from __future__ import annotations
from .contract import ResearchRun, VALID_STATUS, ALLOWED_KINDS
from .store import ResearchRunStore

class RunRegistry:
    def __init__(self, store: ResearchRunStore): self._s = store
    def _validate(self, r: ResearchRun) -> None:
        if r.status not in VALID_STATUS: raise ValueError(f"bad status {r.status!r}")
        if r.kind not in ALLOWED_KINDS: raise ValueError(f"non-whitelisted kind {r.kind!r}")
    def upsert(self, r: ResearchRun) -> None: self._validate(r); self._s.upsert(r)
    def get(self, run_id): return self._s.get(run_id)
    def list(self, kind=None, limit=200): return self._s.list(kind, limit)
    def set_status(self, run_id, status, error=None):
        if status not in VALID_STATUS: raise ValueError(f"bad status {status!r}")
        self._s.set_status(run_id, status, error)
```

`__init__.py` exports `ResearchRun, make_run_id, ResearchRunStore, RunRegistry`.

- [ ] **Step 6: Run tests, verify pass** — `rtk proxy python -m pytest tests/research/test_run_registry.py -q` → PASS.

- [ ] **Step 7: Commit** — `git add services/research/run_registry tests/research/test_run_registry.py && git commit` (msg: `feat(research): run_registry——研究运行登记表(sqlite,fail-closed,只研究 kind)`) then `git branch -f main master`.

---

### Task 2: indexer — existing result JSONs → ResearchRun records

**Files:** Create `services/research/run_registry/indexer.py`; Test `tests/research/test_run_indexer.py`

**Interfaces:**
- Consumes: `RunRegistry`, `ResearchRun`, `make_run_id` (Task 1).
- Produces: `index_existing(cache_dir: str, registry: RunRegistry, *, now: int) -> int` (returns count indexed). Per-kind adapters `adapt_carry(j)`, `adapt_oi_research(j)`, `adapt_combo(j)`, `adapt_survivorship(j)`, `adapt_oi_verify(j)`, `adapt_factory(kind, j)` each return `dict(metrics=, series=, verdict=, caveats=, title=, provenance=)`. Map of `{filename: (kind, adapter)}` named `SOURCES`.

- [ ] **Step 1: Write failing test** — feed a tiny fake carry JSON + assert one record with the right metrics/verdict:

```python
import json
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.indexer import index_existing, adapt_carry

def test_adapt_carry_extracts_headline():
    j = {"n_instruments": 115, "n_days": 2186, "sharpe_net_5bps": 2.03, "avg_funding_bps_day": 5.43,
         "sharpe_price_plus_funding": 2.25, "signal_expr": "(...)"}
    out = adapt_carry(j)
    assert out["metrics"]["net_sharpe_5bps"] == 2.03 and out["verdict"]

def test_index_existing_writes_records(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "crypto_carry_strategy.json").write_text(json.dumps(
        {"sharpe_net_5bps": 2.03, "avg_funding_bps_day": 5.43, "n_instruments": 115, "n_days": 2186}))
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    n = index_existing(str(cache), reg, now=1000)
    assert n == 1
    runs = reg.list(kind="carry_strategy")
    assert len(runs) == 1 and runs[0].metrics["net_sharpe_5bps"] == 2.03 and runs[0].status == "done"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `indexer.py`** — `SOURCES = {"crypto_carry_strategy.json": ("carry_strategy", adapt_carry), "crypto_oi_research.json": ("oi_research", adapt_oi_research), "crypto_combo_book.json": ("combo_book", adapt_combo), "crypto_oi_survivorship.json": ("survivorship", adapt_survivorship), "crypto_oi_verify.json": ("oi_research", adapt_oi_verify), "crypto_factory_orthogonal.json": ("factory_orthogonal", lambda j: adapt_factory("factory_orthogonal", j)), "crypto_factory_loop.json": ("factory_loop", lambda j: adapt_factory("factory_loop", j))}`. Each adapter pulls the headline numbers already present in those JSONs (carry: `sharpe_net_5bps`,`avg_funding_bps_day`; oi_research: read `factors`/`carry_econ_sharpe`; combo: `combo_econ_sharpe`,`combo_max_drawdown`,`combo_per_year`; survivorship: `survivors_only_sharpe`,`complete_sharpe`,`verdict`; factory: `verdict`,`discoveries`). `index_existing` loops `SOURCES`, loads each present file, builds `ResearchRun(run_id=make_run_id(kind, {"source": fname}, now), status="done", created_ts=now, finished_ts=now, **adapter(j))`, `registry.upsert`. Skips missing files.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** (`feat(research): 索引器——现有回测结果 JSON 灌入 run_registry`).

---

### Task 3: console_api read endpoints + contract

**Files:** MODIFY `services/research/console_api/main.py`; MODIFY `tests/research/test_console_api_contract.py`, `services/research/console_api/tests/test_console_api.py`

**Interfaces:**
- Consumes: `RunRegistry`, `ResearchRunStore` (Task 1), `index_existing` (Task 2).
- Produces: `GET /api/v1/console/research-runs` → `{"source":"BACKEND_COMPUTED_SNAPSHOT","read_only":True,"runs":[{run_id,kind,title,status,created_ts,headline:{label,value},verdict}]}`; `GET /api/v1/console/research-runs/{run_id}` → `{"source":...,"read_only":True,"run":{full ResearchRun.to_dict() or None}}`. Registry db path `data/cache/research_runs.db`; lazily index existing JSONs on first read if the table is empty.

- [ ] **Step 1: Write failing contract+unit tests** — assert top-level keys + that the seeded/indexed runs list is non-empty and each summary has the pinned keys; detail endpoint returns the full run for a known id and `{"run": None}` for an unknown id.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add a module-level `_runs_registry()` helper that opens `ResearchRunStore("data/cache/research_runs.db")`, and if `store.list(limit=1)` is empty calls `index_existing("data/cache", RunRegistry(store), now=<fixed pinned ts>)` (deterministic ts constant, not wall-clock). Two `@app.get` handlers building the summary list (headline picked per kind) + detail. Keep GET-only.
- [ ] **Step 4: Run tests, verify pass; run `python scripts/validate.py`** → green.
- [ ] **Step 5: Commit** (`feat(console-api): 只读 research-runs 列表/详情端点 + 契约`).

---

### Task 4: frontend Research Runs section + generic viewer (read-only)

**Files:** Create `apps/research-console/src/pages/ResearchRuns.tsx`, `RunDetail.tsx`; MODIFY `src/lib/api/research-console-api.ts` (add `fetchResearchRuns`, `fetchResearchRun(id)`), `src/lib/types/research.ts` (add `ResearchRunSummary`, `ResearchRunDetail`), `src/App.tsx` (nav group "研究中心" + routes), `src/pages/__tests__/pages.smoke.test.tsx`

**Interfaces:**
- Consumes: the two GET endpoints (Task 3).
- Produces: `/research/runs` (list) and `/research/runs/:id` (detail) routes. `RunDetail` is GENERIC: KpiStrip from `metrics`, `BarSeries` per `series` entry, `StatusChip` from `verdict`, caveats list, provenance footer.

- [ ] **Step 1: Add TS types** (`ResearchRunSummary {run_id,kind,title,status,created_ts,headline:{label,value},verdict}`, `ResearchRunDetail {...full}`) + mock fixtures.
- [ ] **Step 2: Write smoke test** — add `ResearchRuns` + `RunDetail` to `pages.smoke.test.tsx` (mock mode renders fixtures).
- [ ] **Step 3: Run, verify fail** (`npm test`).
- [ ] **Step 4: Implement api client + pages** — `ResearchRuns` lists `useConsoleSnapshot(fetchResearchRuns)` with mock fallback (status chips, kind filter, headline column, link to detail); `RunDetail` reads `fetchResearchRun(id)` and renders generically. ProvenanceRibbon variant honest about research-data.
- [ ] **Step 5: Wire nav + routes in `App.tsx`** (new group "研究中心 / Research Runs": list + a back-compat route for detail). Use unique nav icon (`RR`).
- [ ] **Step 6: Run `npm run typecheck && npm test && npm run build`** → green.
- [ ] **Step 7: Browser-verify** — preview_start, open `/research/runs`, screenshot list + a detail; confirm generic render + no console errors.
- [ ] **Step 8: Commit** (`feat(frontend): 研究中心——动态运行列表 + 通用数据驱动查看器`).

---

### Task 5: research_runner service (guarded trigger + async queue) + governance

**Files:** Create `services/research/research_runner/__init__.py`, `run_kinds.py`, `runner.py`, `main.py`; Test `tests/research/test_research_runner.py`, `tests/governance/test_research_runner_boundary.py`

**Interfaces:**
- Consumes: `RunRegistry`, `ResearchRun`, `make_run_id` (Task 1).
- Produces: `RUN_KINDS: dict[str, RunSpec]` where `RunSpec=dataclass(entrypoint:str  # "module:func", param_schema:dict[str,type], adapter:callable, title:str)`; `validate_params(kind, params)->dict`; `JobQueue(registry, max_workers=2)` with `submit(kind, params, now)->str` (returns run_id; writes `queued`, runs async → `running`→`done`/`error`); FastAPI `app` with `POST /run` and `GET /health`.

- [ ] **Step 1: Write failing unit tests** — `tests/research/test_research_runner.py`:

```python
import pytest, time
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research.research_runner.run_kinds import RUN_KINDS, validate_params
from services.research.research_runner.runner import JobQueue

def test_whitelist_is_research_only():
    assert set(RUN_KINDS) >= {"carry_strategy", "oi_research", "combo_book"}
    for spec in RUN_KINDS.values():
        root = spec.entrypoint.split(":")[0].split(".")[0]
        assert root in {"services", "tools"}

def test_validate_params_rejects_unknown_key():
    with pytest.raises(ValueError):
        validate_params("carry_strategy", {"q": 0.3, "evil": "rm -rf"})

def test_unknown_kind_rejected():
    with pytest.raises(KeyError):
        validate_params("place_order", {})

def test_job_runs_and_writes_registry(tmp_path, monkeypatch):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    # inject a stub kind that needs no network/compute
    from services.research.research_runner import run_kinds as rk
    from services.research.research_runner.run_kinds import RunSpec
    rk.RUN_KINDS["_stub"] = RunSpec(entrypoint="services._x:noop", param_schema={"n": int},
        adapter=lambda out: {"title": "stub", "params": {}, "metrics": {"v": out}, "series": {},
                             "verdict": "DONE", "caveats": [], "provenance": "stub"},
        title="stub", runner=lambda params: params["n"] * 2)
    q = JobQueue(reg, max_workers=1)
    rid = q.submit("_stub", {"n": 21}, now=1000)
    q.join()
    got = reg.get(rid)
    assert got.status == "done" and got.metrics["v"] == 42
    del rk.RUN_KINDS["_stub"]

def test_job_error_sets_error_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    from services.research.research_runner import run_kinds as rk
    from services.research.research_runner.run_kinds import RunSpec
    rk.RUN_KINDS["_boom"] = RunSpec(entrypoint="services._x:boom", param_schema={},
        adapter=lambda out: {}, title="boom", runner=lambda params: (_ for _ in ()).throw(RuntimeError("x")))
    q = JobQueue(reg, max_workers=1); rid = q.submit("_boom", {}, now=1); q.join()
    assert reg.get(rid).status == "error" and reg.get(rid).error
    del rk.RUN_KINDS["_boom"]
```

(Note: `RunSpec` carries an in-process `runner` callable for testability; production specs build it from `entrypoint` via a small importer. The `param_schema` validates keys+types; `validate_params` drops/raises on unknown keys.)

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `run_kinds.py`** — `RunSpec` dataclass; `RUN_KINDS` mapping each whitelisted kind to entrypoint + param_schema + adapter (reuse Task 2 adapters) + a `runner` built lazily from `entrypoint` (`importlib`). `validate_params(kind, params)`: `spec=RUN_KINDS[kind]` (KeyError if unknown); for each key assert in schema and `isinstance(value, schema[key])`; return the filtered dict. Param schemas are small + typed (e.g. `carry_strategy: {}`; `oi_research: {}`; `data_refresh: {"intraday": bool}`; `factory_*: {"generations": int, "population": int}`) — **no free-form strings that reach a path/shell**.
- [ ] **Step 4: Implement `runner.py`** — `JobQueue(registry, max_workers=2)` using `concurrent.futures.ThreadPoolExecutor`; `submit(kind, params, now)`: `validate_params`; `run_id=make_run_id(kind, params, now)`; `registry.upsert(ResearchRun(...status="queued", created_ts=now...))`; `executor.submit(self._run, kind, params, run_id, now)`; return run_id. `_run`: `set_status(running)`; `out=RUN_KINDS[kind].runner(params)`; `adapted=RUN_KINDS[kind].adapter(out)`; `registry.upsert(ResearchRun(run_id, kind, status="done", finished_ts=now, **adapted))`; except → `set_status(error, str(e))`. `join()` for tests.
- [ ] **Step 5: Implement `main.py`** — FastAPI `app`; module `JobQueue` over `ResearchRunStore("data/cache/research_runs.db")`; `POST /run` body `{kind, params}` → `try: rid=queue.submit(...); return {"run_id":rid,"status":"queued"}` `except (KeyError,ValueError) as e: raise HTTPException(400, str(e))`; `GET /health` → `{"mode":"RESEARCH_RUNNER","live_disallowed":True,"kinds":sorted(RUN_KINDS)}`. CORS allow `:3100` only. **No GET that runs anything; no order/flag path.**
- [ ] **Step 6: Write governance test** `tests/governance/test_research_runner_boundary.py` (AST-scan the runner package, mirroring `test_crypto_data_boundary.py`):

```python
import ast, os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PKG = os.path.join(_ROOT, "services", "research", "research_runner")
_FORBIDDEN_SDK = {"ccxt","binance","ibapi","alpaca","oandapyV20","fix","quickfix","ctp","vnpy"}
_FORBIDDEN_CALLS = ["create_order","place_order","submit_order","cancel_order","fapiPrivate","sapiPost",".withdraw(","transfer("]

def _roots(p):
    t = ast.parse(open(p, encoding="utf-8").read()); out=set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import): out|={a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level==0: out.add(n.module.split(".")[0])
    return out

def _pyfiles(): return [os.path.join(_PKG,f) for f in os.listdir(_PKG) if f.endswith(".py")]

def test_runner_no_broker_sdk_or_trading_import():
    for p in _pyfiles():
        roots=_roots(p)
        assert not (roots & _FORBIDDEN_SDK), f"broker SDK in {p}: {roots & _FORBIDDEN_SDK}"
        src=open(p, encoding="utf-8").read()
        assert "services.trading" not in src and "from services.trading" not in src
        assert not any(c in src for c in _FORBIDDEN_CALLS), f"trade/transfer call in {p}"

def test_whitelist_entrypoints_are_research_only():
    from services.research.research_runner.run_kinds import RUN_KINDS
    for k, spec in RUN_KINDS.items():
        assert spec.entrypoint.split(":")[0].split(".")[0] in {"services","tools"}
        assert "trading" not in spec.entrypoint and "order" not in spec.entrypoint
        assert isinstance(spec.param_schema, dict)   # every kind has a (possibly empty) typed schema
```

- [ ] **Step 7: Run** `rtk proxy python -m pytest tests/research/test_research_runner.py tests/governance/test_research_runner_boundary.py -q` → PASS; then `python scripts/validate.py` → green.
- [ ] **Step 8: Commit** (`feat(research): research_runner——受限研究运行器(白名单/异步队列/治理钉死,绝不碰交易)`).

---

### Task 6: frontend trigger UI + live status polling + e2e

**Files:** Create `apps/research-console/src/lib/api/research-runner-api.ts`; MODIFY `ResearchRuns.tsx` (add run buttons + polling)

**Interfaces:**
- Consumes: runner `POST /run` (this task) + console_api `/research-runs` polling (Task 3/4).
- Produces: `triggerRun(kind, params)->Promise<{run_id,status}>` against `VITE_RUNNER_URL` (default '' → buttons disabled with "启动 runner" hint).

- [ ] **Step 1: Implement `research-runner-api.ts`** — `RESEARCH_RUNNER_BASE = import.meta.env.VITE_RUNNER_URL || ''`; `runnerEnabled()`; `triggerRun(kind, params)` POST.
- [ ] **Step 2: Add run buttons + polling to `ResearchRuns.tsx`** — a row of buttons (one per kind from runner `/health`, or a static list), disabled when `!runnerEnabled()` with a tooltip; on click → `triggerRun` → optimistic insert (queued) → `setInterval` re-fetch `/research-runs` every 3s until no run is `queued`/`running` (clear interval on unmount). Honest banner "研究运行 · 不下单/不碰实盘 · L2".
- [ ] **Step 3: Add `VITE_RUNNER_URL` to vite.config define + `research-console-live` launch.json env** (`http://127.0.0.1:8420`).
- [ ] **Step 4: typecheck + test + build** → green.
- [ ] **Step 5: e2e browser-verify** — start console_api (8400) + research_runner (8420, `rtk proxy python -m uvicorn services.research.research_runner.main:app --port 8420`) + live frontend; click a fast kind (e.g. `combo_book`), watch it go queued→running→done, open its detail. Screenshot.
- [ ] **Step 6: Commit** (`feat(frontend): 研究中心触发 UI——前端按钮跑研究 + 实时状态轮询`).

---

### Task 7: docs + final gate

- [ ] **Step 1:** Update `CLAUDE.md` Commands (how to start research_runner) + `docs/PROJECT_STATE.md` + memory.
- [ ] **Step 2:** `python scripts/validate.py` green + frontend gate green; push `origin master`.
- [ ] **Step 3: Commit + push** (`docs: 研究操作台落地——动态结果区 + 前端触发研究运行(L2 安全不变)`).

---

## Self-Review

**Spec coverage:** §3 run_registry→Task1; indexer→Task2; §4 console_api read→Task3; §6 frontend viewer→Task4; §5 research_runner+safety→Task5; §6 trigger UI→Task6; §8 testing folded into each task; §9 build order = Task order. ✓
**Placeholder scan:** all steps have concrete code/commands. ✓
**Type consistency:** `ResearchRun`/`RunRegistry`/`make_run_id` signatures identical across Tasks 1–5; `RunSpec(entrypoint, param_schema, adapter, title, runner)` consistent in Task 5 impl + tests + governance. ✓
**Safety coverage:** governance test (Task 5 Step 6) machine-enforces no-broker/no-trading/no-order + research-only whitelist; console_api stays GET-only (Task 3); auto_approved untouched. ✓
