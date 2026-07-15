"""JobQueue — adapter-side execution of whitelisted research runs with durable logs.

submit() validates (unknown kind / bad param -> raise), idempotently resolves a caller invocation,
records git sha (auditable history), writes a `queued` record for a new invocation, and runs the kind's
research function on a small thread pool: queued -> running ->
done/error/cancelled. While running, the tool's stdout is streamed line-by-line into the run's log so a
long search is observable in real time. cancel() is a SOFT cancel: it marks the run cancelled and drops
its result (a running tool cannot be force-killed safely). It mutates NOTHING but the research registry;
it never touches orders/broker/credentials/flags (the whitelist has no such entry)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from services.research.run_registry.contract import (
    RUN_OWNER_RUNNER_INVOCATION,
    InvocationConflictError,
    ResearchRun,
    make_request_hash,
    make_run_id,
    make_run_identity_hash,
    normalize_invocation_id,
)
from .run_kinds import RUN_KINDS, RunPurpose, RunSpec, validate_params

_PROVENANCE_SCHEMA = "quantos.research_runner.provenance.v1"
_PROVENANCE_KEYS = {
    "schema",
    "kind",
    "purpose",
    "selection_eligible",
    "evidence_authority",
    "ledger_binding_status",
    "result_authority",
    "entrypoint",
    "source_artifact",
    "code_version",
    "code_attestation",
    "result_sha256",
}


class ProvenanceError(ValueError):
    """A run cannot be bound to its declared code, source, purpose, and result."""


def _source_artifact(entrypoint: str) -> str:
    module, separator, function = entrypoint.partition(":")
    if not separator or not module or not function:
        raise ProvenanceError(f"invalid research entrypoint {entrypoint!r}")
    return module.replace(".", "/") + ".py"


def _sha256_json(value) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"research result is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _is_git_commit_prefix(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 12
        and all(character in "0123456789abcdef" for character in value)
    )


def _result_digest(
    *,
    title: str,
    params: dict,
    metrics: dict,
    series: dict,
    verdict: str,
    caveats: list,
) -> str:
    return _sha256_json(
        {
            "title": title,
            "params": params,
            "metrics": metrics,
            "series": series,
            "verdict": verdict,
            "caveats": caveats,
        }
    )


def _build_provenance(
    kind: str,
    spec: RunSpec,
    code_version: str | None,
    *,
    result_sha256: str | None = None,
) -> str:
    policy = spec.provenance_policy
    if code_version is not None and not _is_git_commit_prefix(code_version):
        raise ProvenanceError("run code version is not a resolved git commit prefix")
    if policy.purpose is RunPurpose.SELECTION_BEARING and not _is_git_commit_prefix(code_version):
        raise ProvenanceError("selection-bearing runs require a resolved code version")
    document = {
        "schema": _PROVENANCE_SCHEMA,
        "kind": kind,
        "purpose": policy.purpose.value,
        # The run-registry row is only a console/operations mirror. Current factory JSON does not
        # contain an exact search_id + dataset_version + ledger-head reference, so claiming that this
        # row itself is selection evidence would be a false green.
        "selection_eligible": False,
        "evidence_authority": policy.evidence_authority,
        "ledger_binding_status": (
            "UNBOUND"
            if policy.purpose is RunPurpose.SELECTION_BEARING
            else "NOT_APPLICABLE"
        ),
        "result_authority": "NON_AUTHORITATIVE_RUN_REGISTRY_MIRROR",
        "entrypoint": spec.entrypoint,
        "source_artifact": _source_artifact(spec.entrypoint),
        "code_version": code_version or "UNAVAILABLE",
        # A HEAD prefix does not attest that the worktree is clean. Keep it for correlation only.
        "code_attestation": (
            "HEAD_ONLY_WORKTREE_UNATTESTED" if code_version else "UNAVAILABLE"
        ),
        "result_sha256": result_sha256,
    }
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def validate_run_provenance(run: ResearchRun, spec: RunSpec) -> dict:
    """Validate the structured provenance persisted on a runner-owned run.

    All run-registry records are non-authoritative mirrors. Selection-bearing tools identify the global
    trial ledger as their evidence authority, but remain ineligible until a future exact ledger binding
    can be verified; a canonical result digest alone is not such a binding.
    """
    try:
        document = json.loads(run.provenance)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvenanceError("run provenance must be canonical JSON") from exc
    if not isinstance(document, dict) or set(document) != _PROVENANCE_KEYS:
        raise ProvenanceError("run provenance schema keys do not match the runner contract")

    policy = spec.provenance_policy
    expected = {
        "schema": _PROVENANCE_SCHEMA,
        "kind": run.kind,
        "purpose": policy.purpose.value,
        "selection_eligible": False,
        "evidence_authority": policy.evidence_authority,
        "ledger_binding_status": (
            "UNBOUND"
            if policy.purpose is RunPurpose.SELECTION_BEARING
            else "NOT_APPLICABLE"
        ),
        "result_authority": "NON_AUTHORITATIVE_RUN_REGISTRY_MIRROR",
        "entrypoint": spec.entrypoint,
        "source_artifact": _source_artifact(spec.entrypoint),
        "code_version": run.code_version or "UNAVAILABLE",
        "code_attestation": (
            "HEAD_ONLY_WORKTREE_UNATTESTED" if run.code_version else "UNAVAILABLE"
        ),
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ProvenanceError(f"run provenance {key!r} does not match the runner policy")

    if run.status == "done":
        expected_digest = _result_digest(
            title=run.title,
            params=run.params,
            metrics=run.metrics,
            series=run.series,
            verdict=run.verdict,
            caveats=run.caveats,
        )
        if document["result_sha256"] != expected_digest:
            raise ProvenanceError("run provenance result digest does not match the persisted result")
    elif document["result_sha256"] is not None:
        raise ProvenanceError("non-complete runs cannot claim a result digest")

    if run.code_version is not None and not _is_git_commit_prefix(run.code_version):
        raise ProvenanceError("run code version is not a resolved git commit prefix")
    if (
        policy.purpose is RunPurpose.SELECTION_BEARING
        and not _is_git_commit_prefix(run.code_version)
    ):
        raise ProvenanceError("selection-bearing runs require a resolved code version")
    return document


def _read_ref(git_dir: str, ref: str) -> str | None:
    """Resolve `ref` (e.g. 'refs/heads/main') rooted at `git_dir`: a loose ref file if
    present, else a `packed-refs` entry (git packs infrequently-updated refs there and
    drops the loose file). Returns None (never raises) if neither resolves."""
    loose = os.path.join(git_dir, ref)
    if os.path.isfile(loose):
        return open(loose, encoding="utf-8").read().strip()
    packed = os.path.join(git_dir, "packed-refs")
    if os.path.isfile(packed):
        for line in open(packed, encoding="utf-8"):
            line = line.strip()
            if not line or line[0] in "#^":
                continue
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    return None


def _git_sha() -> str | None:
    """Current commit sha (pure file read of .git — no subprocess/shell). None if unavailable.

    Handles all layouts:
      - plain clone: .git is a DIRECTORY containing HEAD (+ refs/...).
      - git worktree (`git worktree add`): .git is a FILE containing a single line
        'gitdir: <path to the private per-worktree git dir>'; HEAD lives there.
        Detached HEAD in a worktree: HEAD holds the sha directly, resolved below with no
        further indirection. HEAD on a branch (the default/typical `git worktree add`
        state): the private gitdir's own refs/ tree is EMPTY — the branch ref actually
        lives in the shared/common gitdir, reached via this private gitdir's `commondir`
        file (a path, usually relative e.g. '../..', to the common gitdir).
      - packed refs: the branch ref may have no loose file at all, only a `packed-refs`
        entry (in whichever gitdir — private or common — actually holds it).
    """
    try:
        git_dir = ".git"
        if os.path.isfile(git_dir):
            pointer = open(git_dir, encoding="utf-8").read().strip()
            if not pointer.startswith("gitdir:"):
                return None
            git_dir = pointer.split(":", 1)[1].strip()
        head = open(os.path.join(git_dir, "HEAD"), encoding="utf-8").read().strip()
        if not head.startswith("ref:"):
            return head[:12]
        ref = head.split(" ", 1)[1].strip()

        sha = _read_ref(git_dir, ref)
        if sha is None:
            commondir_file = os.path.join(git_dir, "commondir")
            if os.path.isfile(commondir_file):
                commondir = open(commondir_file, encoding="utf-8").read().strip()
                common_gitdir = os.path.normpath(os.path.join(git_dir, commondir))
                sha = _read_ref(common_gitdir, ref)
        return sha[:12] if sha else None
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

    def submit(
        self,
        kind: str,
        params: dict,
        now: int,
        operator: str = None,
        invocation_id: str | None = None,
    ) -> str:
        spec = RUN_KINDS[kind]                          # KeyError on unknown kind (fail-closed)
        clean = validate_params(kind, params)
        if invocation_id is None:
            invocation_id = uuid.uuid4().hex
        invocation_id = normalize_invocation_id(invocation_id)
        request_hash = make_request_hash(kind, clean, operator)
        existing = self._reg.get_by_invocation(invocation_id)
        if existing is not None:
            if (
                existing.request_hash == request_hash
                and existing.kind == kind
                and existing.params == clean
                and existing.operator == operator
            ):
                validate_run_provenance(existing, spec)
                return existing.run_id
            raise InvocationConflictError(
                "invocation_id is already bound to a different kind, params, or operator"
            )
        run_id = make_run_id(
            kind,
            clean,
            now,
            invocation_id=invocation_id,
        )
        code_version = _git_sha()
        identity_hash = make_run_identity_hash(
            run_id=run_id,
            kind=kind,
            params=clean,
            created_ts=now,
            operator=operator,
            code_version=code_version,
            invocation_id=invocation_id,
            owner=RUN_OWNER_RUNNER_INVOCATION,
        )
        queued = ResearchRun(
            run_id=run_id,
            kind=kind,
            title=spec.title,
            params=clean,
            status="queued",
            created_ts=now,
            finished_ts=None,
            metrics={},
            series={},
            verdict="",
            caveats=[],
            provenance=_build_provenance(kind, spec, code_version),
            log=["[queued]"],
            code_version=code_version,
            operator=operator,
            invocation_id=invocation_id,
            owner=RUN_OWNER_RUNNER_INVOCATION,
            request_hash=request_hash,
            identity_hash=identity_hash,
        )
        validate_run_provenance(queued, spec)
        persisted, inserted = self._reg.insert_or_get(queued)
        if not inserted:
            validate_run_provenance(persisted, spec)
            return persisted.run_id
        self._futures.append(
            self._ex.submit(
                self._run,
                kind,
                clean,
                run_id,
                now,
                operator,
                code_version,
                invocation_id,
                request_hash,
                identity_hash,
            )
        )
        return run_id

    def cancel(self, run_id: str) -> bool:
        """Soft cancel: mark cancelled now; if the job is still running its result is dropped on finish.
        Returns False if the run is already in a terminal state."""
        r = self._reg.get(run_id)
        if r is None or r.status in {"done", "error", "cancelled"}:
            return False
        changed = self._reg.compare_and_set_status(
            run_id,
            expected_statuses=("queued", "running"),
            status="cancelled",
        )
        if changed:
            self._cancelled.add(run_id)
            self._reg.append_log(run_id, "[cancel requested]")
        return changed

    def _run(
        self,
        kind: str,
        params: dict,
        run_id: str,
        now: int,
        operator: str = None,
        code_version: str | None = None,
        invocation_id: str | None = None,
        request_hash: str | None = None,
        identity_hash: str | None = None,
    ) -> None:
        spec = RUN_KINDS[kind]
        try:
            if not self._reg.compare_and_set_status(
                run_id,
                expected_statuses=("queued",),
                status="running",
            ):
                self._cancelled.discard(run_id)
                return
            self._reg.append_log(run_id, "[running]")
            with contextlib.redirect_stdout(_LogWriter(self._reg, run_id)):
                out = spec.resolve()(params)
            if run_id in self._cancelled:               # cancelled mid-run -> drop result, keep cancelled
                self._cancelled.discard(run_id)
                self._reg.append_log(run_id, "[cancelled — result discarded]")
                return
            a = spec.adapter(out)
            if not isinstance(a, dict):
                raise ProvenanceError("research adapter must return a mapping")
            declared_source = a.get("provenance")
            expected_source = _source_artifact(spec.entrypoint)
            if declared_source != expected_source:
                raise ProvenanceError(
                    f"adapter source {declared_source!r} does not match {expected_source!r}"
                )
            prev = self._reg.get(run_id)                # preserve the live log accumulated during the run
            title = a.get("title", spec.title)
            metrics = a.get("metrics", {})
            series = a.get("series", {})
            verdict = a.get("verdict", "")
            caveats = a.get("caveats", [])
            result_sha256 = _result_digest(
                title=title,
                params=params,
                metrics=metrics,
                series=series,
                verdict=verdict,
                caveats=caveats,
            )
            completed = ResearchRun(
                run_id=run_id, kind=kind, status="done", created_ts=now, finished_ts=now,
                title=title, params=params, metrics=metrics, series=series,
                verdict=verdict, caveats=caveats,
                provenance=_build_provenance(
                    kind,
                    spec,
                    code_version,
                    result_sha256=result_sha256,
                ),
                log=(prev.log if prev else []) + ["[done]"],
                code_version=code_version,
                operator=operator,
                invocation_id=invocation_id,
                owner=RUN_OWNER_RUNNER_INVOCATION,
                request_hash=request_hash,
                identity_hash=identity_hash,
            )
            validate_run_provenance(completed, spec)
            published = self._reg.replace_if_status(
                completed,
                expected_status="running",
            )
            if not published:
                self._cancelled.discard(run_id)
        except Exception as e:                          # any failure -> visible error status, never a crash
            changed = self._reg.compare_and_set_status(
                run_id,
                expected_statuses=("running",),
                status="error",
                error=str(e),
            )
            if changed:
                self._reg.append_log(run_id, f"[error] {e}")
            self._cancelled.discard(run_id)

    def join(self) -> None:
        for f in self._futures:
            f.result()
        self._futures = []
