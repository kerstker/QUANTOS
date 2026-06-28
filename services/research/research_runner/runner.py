"""JobQueue — async execution of WHITELISTED research runs, writing status + LIVE LOG to the run_registry.

submit() validates (unknown kind / bad param -> raise), records git sha (auditable history), writes a
`queued` record, and runs the kind's research function on a small thread pool: queued -> running ->
done/error/cancelled. While running, the tool's stdout is streamed line-by-line into the run's log so a
long search is observable in real time. cancel() is a SOFT cancel: it marks the run cancelled and drops
its result (a running tool cannot be force-killed safely). It mutates NOTHING but the research registry;
it never touches orders/broker/credentials/flags (the whitelist has no such entry)."""
from __future__ import annotations

import contextlib
import io
import os
from concurrent.futures import ThreadPoolExecutor

from services.research.run_registry.contract import ResearchRun, make_run_id
from .run_kinds import RUN_KINDS, validate_params


def _git_sha() -> str | None:
    """Current commit sha (pure file read of .git — no subprocess/shell). None if unavailable."""
    try:
        head = open(os.path.join(".git", "HEAD"), encoding="utf-8").read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            return open(os.path.join(".git", ref), encoding="utf-8").read().strip()[:12]
        return head[:12]
    except Exception:
        return None


class _LogWriter(io.TextIOBase):
    """A stdout sink that appends each completed line to the run's live log in the registry."""

    def __init__(self, registry, run_id: str):
        self._reg = registry
        self._run_id = run_id
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._reg.append_log(self._run_id, line.rstrip())
        return len(s)


class JobQueue:
    def __init__(self, registry, max_workers: int = 2):
        self._reg = registry
        self._ex = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: list = []
        self._cancelled: set = set()

    def submit(self, kind: str, params: dict, now: int) -> str:
        spec = RUN_KINDS[kind]                          # KeyError on unknown kind (fail-closed)
        clean = validate_params(kind, params)
        run_id = make_run_id(kind, clean, now)
        self._reg.upsert(ResearchRun(run_id=run_id, kind=kind, title=spec.title, params=clean,
                                     status="queued", created_ts=now, finished_ts=None,
                                     metrics={}, series={}, verdict="", caveats=[],
                                     provenance=spec.entrypoint, log=["[queued]"], code_version=_git_sha()))
        self._futures.append(self._ex.submit(self._run, kind, clean, run_id, now))
        return run_id

    def cancel(self, run_id: str) -> bool:
        """Soft cancel: mark cancelled now; if the job is still running its result is dropped on finish.
        Returns False if the run is already in a terminal state."""
        r = self._reg.get(run_id)
        if r is None or r.status in {"done", "error", "cancelled"}:
            return False
        self._cancelled.add(run_id)
        self._reg.append_log(run_id, "[cancel requested]")
        self._reg.set_status(run_id, "cancelled")
        return True

    def _run(self, kind: str, params: dict, run_id: str, now: int) -> None:
        spec = RUN_KINDS[kind]
        try:
            self._reg.set_status(run_id, "running")
            self._reg.append_log(run_id, "[running]")
            with contextlib.redirect_stdout(_LogWriter(self._reg, run_id)):
                out = spec.resolve()(params)
            if run_id in self._cancelled:               # cancelled mid-run -> drop result, keep cancelled
                self._cancelled.discard(run_id)
                self._reg.append_log(run_id, "[cancelled — result discarded]")
                return
            a = spec.adapter(out)
            prev = self._reg.get(run_id)                # preserve the live log accumulated during the run
            self._reg.upsert(ResearchRun(
                run_id=run_id, kind=kind, status="done", created_ts=now, finished_ts=now,
                title=a.get("title", spec.title), params=params,
                metrics=a.get("metrics", {}), series=a.get("series", {}),
                verdict=a.get("verdict", ""), caveats=a.get("caveats", []),
                provenance=a.get("provenance", spec.entrypoint),
                log=(prev.log if prev else []) + ["[done]"], code_version=_git_sha()))
        except Exception as e:                          # any failure -> visible error status, never a crash
            self._reg.append_log(run_id, f"[error] {e}")
            self._reg.set_status(run_id, "error", error=str(e))

    def join(self) -> None:
        for f in self._futures:
            f.result()
        self._futures = []
