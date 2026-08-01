"""Append-only authority for factory candidates admitted to forward paper.

The registry grants only L2 forward-paper observation.  It contains no broker,
credential, live-deployment, lifecycle-promotion, or order-submission seam.
Admission evidence is verified by the tooling boundary before insertion; this
store preserves the resulting identity and hash chain without mutation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import sqlite3
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax


class PaperCandidateRegistryError(ValueError):
    """A candidate or append-only registry invariant was violated."""


@dataclass(frozen=True)
class PaperCandidate:
    alpha_id: str
    expression: str
    definition_hash: str
    source_search_id: str
    evaluation_run_id: str
    dataset_version: str
    quantile: float
    cadence_days: int
    admitted_at_ms: int
    admission_class: str = "VERIFIED_FACTORY_PASS_TO_FORWARD_PAPER"


def _validated(candidate: PaperCandidate) -> PaperCandidate:
    if not isinstance(candidate, PaperCandidate):
        raise TypeError("candidate must be a PaperCandidate")
    try:
        expression = ax.deserialize(candidate.expression)
    except (TypeError, ValueError) as exc:
        raise PaperCandidateRegistryError("candidate expression is invalid") from exc
    if ax.alpha_id(expression) != candidate.alpha_id:
        raise PaperCandidateRegistryError("candidate alpha_id does not match expression")
    if (
        len(candidate.definition_hash) != 64
        or any(char not in "0123456789abcdef" for char in candidate.definition_hash.lower())
    ):
        raise PaperCandidateRegistryError("candidate definition_hash is invalid")
    for name in ("source_search_id", "evaluation_run_id", "dataset_version"):
        value = getattr(candidate, name)
        if not isinstance(value, str) or not value.strip():
            raise PaperCandidateRegistryError(f"candidate {name} is required")
    if (
        isinstance(candidate.quantile, bool)
        or not isinstance(candidate.quantile, (int, float))
        or not math.isfinite(float(candidate.quantile))
        or not 0.0 < float(candidate.quantile) <= 0.5
    ):
        raise PaperCandidateRegistryError("candidate quantile is invalid")
    if type(candidate.cadence_days) is not int or not 1 <= candidate.cadence_days <= 30:
        raise PaperCandidateRegistryError("candidate cadence_days is invalid")
    if type(candidate.admitted_at_ms) is not int or candidate.admitted_at_ms < 0:
        raise PaperCandidateRegistryError("candidate admitted_at_ms is invalid")
    if candidate.admission_class != "VERIFIED_FACTORY_PASS_TO_FORWARD_PAPER":
        raise PaperCandidateRegistryError("candidate admission class is invalid")
    return candidate


def _payload(candidate: PaperCandidate) -> dict[str, Any]:
    return asdict(candidate)


class PaperCandidateRegistry:
    """SQLite append-only candidate registry with a single canonical chain."""

    def __init__(self, path: str | Path, *, create: bool = True) -> None:
        resolved = Path(path)
        if create:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(resolved))
        else:
            self._connection = sqlite3.connect(
                f"file:{resolved.resolve()}?mode=ro", uri=True
            )
        self._connection.row_factory = sqlite3.Row
        if create:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_candidates (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                alpha_id TEXT NOT NULL UNIQUE,
                expression TEXT NOT NULL,
                definition_hash TEXT NOT NULL,
                source_search_id TEXT NOT NULL,
                evaluation_run_id TEXT NOT NULL UNIQUE,
                dataset_version TEXT NOT NULL,
                quantile REAL NOT NULL,
                cadence_days INTEGER NOT NULL,
                admitted_at_ms INTEGER NOT NULL,
                admission_class TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS paper_candidates_no_update
            BEFORE UPDATE ON paper_candidates BEGIN
                SELECT RAISE(ABORT, 'paper candidate registry is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS paper_candidates_no_delete
            BEFORE DELETE ON paper_candidates BEGIN
                SELECT RAISE(ABORT, 'paper candidate registry is append-only');
            END;
            """
        )
        self._connection.commit()

    def admit(self, candidate: PaperCandidate) -> bool:
        item = _validated(candidate)
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT * FROM paper_candidates WHERE alpha_id=?",
                (item.alpha_id,),
            ).fetchone()
            if existing is not None:
                if self._candidate_from_row(existing) != item:
                    raise PaperCandidateRegistryError(
                        "candidate alpha_id already has different immutable evidence"
                    )
                connection.rollback()
                return False
            previous = connection.execute(
                "SELECT record_hash FROM paper_candidates ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous is not None else "0" * 64
            record_hash = stable_hash(
                {
                    "previous_hash": previous_hash,
                    "candidate": _payload(item),
                }
            )
            connection.execute(
                """
                INSERT INTO paper_candidates (
                    alpha_id,expression,definition_hash,source_search_id,
                    evaluation_run_id,dataset_version,quantile,cadence_days,
                    admitted_at_ms,admission_class,previous_hash,record_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item.alpha_id,
                    item.expression,
                    item.definition_hash,
                    item.source_search_id,
                    item.evaluation_run_id,
                    item.dataset_version,
                    float(item.quantile),
                    item.cadence_days,
                    item.admitted_at_ms,
                    item.admission_class,
                    previous_hash,
                    record_hash,
                ),
            )
            connection.commit()
            return True
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> PaperCandidate:
        return PaperCandidate(
            alpha_id=str(row["alpha_id"]),
            expression=str(row["expression"]),
            definition_hash=str(row["definition_hash"]),
            source_search_id=str(row["source_search_id"]),
            evaluation_run_id=str(row["evaluation_run_id"]),
            dataset_version=str(row["dataset_version"]),
            quantile=float(row["quantile"]),
            cadence_days=int(row["cadence_days"]),
            admitted_at_ms=int(row["admitted_at_ms"]),
            admission_class=str(row["admission_class"]),
        )

    def all(self) -> tuple[PaperCandidate, ...]:
        rows = self._connection.execute(
            "SELECT * FROM paper_candidates ORDER BY seq"
        ).fetchall()
        return tuple(self._candidate_from_row(row) for row in rows)

    def chain_valid(self) -> bool:
        previous_hash = "0" * 64
        for row in self._connection.execute(
            "SELECT * FROM paper_candidates ORDER BY seq"
        ):
            candidate = self._candidate_from_row(row)
            if str(row["previous_hash"]) != previous_hash:
                return False
            expected = stable_hash(
                {
                    "previous_hash": previous_hash,
                    "candidate": _payload(candidate),
                }
            )
            if str(row["record_hash"]) != expected:
                return False
            previous_hash = expected
        return True

    def snapshot(self) -> dict[str, Any]:
        candidates = self.all()
        return {
            "schema_version": 1,
            "status": "VERIFIED" if self.chain_valid() else "INVALID",
            "candidate_count": len(candidates),
            "candidates": [_payload(candidate) for candidate in candidates],
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PaperCandidateRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "PaperCandidate",
    "PaperCandidateRegistry",
    "PaperCandidateRegistryError",
]
