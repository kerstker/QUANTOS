"""JobQueue — async execution of WHITELISTED research runs, writing status + results to the run_registry.

submit() validates (unknown kind / bad param -> raise), writes a `queued` record, and runs the kind's
research function on a small thread pool: queued -> running -> done (or error). Status lives in the
registry (sqlite), so a frontend polls the read-only console API for progress. It mutates NOTHING but the
research registry; it never touches orders/broker/credentials/flags (the whitelist has no such entry)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.research.run_registry.contract import ResearchRun, make_run_id
from .run_kinds import RUN_KINDS, validate_params


class JobQueue:
    def __init__(self, registry, max_workers: int = 2):
        self._reg = registry
        self._ex = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: list = []

    def submit(self, kind: str, params: dict, now: int) -> str:
        spec = RUN_KINDS[kind]                          # KeyError on unknown kind (fail-closed)
        clean = validate_params(kind, params)
        run_id = make_run_id(kind, clean, now)
        self._reg.upsert(ResearchRun(run_id=run_id, kind=kind, title=spec.title, params=clean,
                                     status="queued", created_ts=now, finished_ts=None,
                                     metrics={}, series={}, verdict="", caveats=[],
                                     provenance=spec.entrypoint))
        self._futures.append(self._ex.submit(self._run, kind, clean, run_id, now))
        return run_id

    def _run(self, kind: str, params: dict, run_id: str, now: int) -> None:
        spec = RUN_KINDS[kind]
        try:
            self._reg.set_status(run_id, "running")
            out = spec.resolve()(params)
            a = spec.adapter(out)
            self._reg.upsert(ResearchRun(
                run_id=run_id, kind=kind, status="done", created_ts=now, finished_ts=now,
                title=a.get("title", spec.title), params=params,
                metrics=a.get("metrics", {}), series=a.get("series", {}),
                verdict=a.get("verdict", ""), caveats=a.get("caveats", []),
                provenance=a.get("provenance", spec.entrypoint)))
        except Exception as e:                          # any failure -> visible error status, never a crash
            self._reg.set_status(run_id, "error", error=str(e))

    def join(self) -> None:
        for f in self._futures:
            f.result()
        self._futures = []
