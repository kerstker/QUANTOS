"""Bounded original-pilot observations, separate from trading and cash stores.

Canonical decision/result bytes and hashes are unchanged. Sampling claims and
all stored bytes consume one original window across process restarts. Independent
local audit witnesses detect missing storage; they are not off-host authority.
"""

from copy import deepcopy
from functools import lru_cache
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from tools.demo_resident_recovery import _check_path
from services.governance.json_integrity import load_json_without_duplicate_keys as decode

SCHEMA = "GOLD_PILOT_OBSERVATION_RETENTION_V1"
AREA = "gold-pilot-decisions"
MARKER = "gold-pilot-retention.json"
AUDIT = "gold-pilot-retention-audit"
INTERVAL_MS = 1000
CLOSING_MS = 900000
MAX_RECORD_BYTES = 16_000_000
MAX_TOTAL_BYTES = 4 * 1024**3
PAGE_BYTES = 4096
CLAIM_MAX_BYTES = 1024
EVENT_MAX_BYTES = 1024
EVENT_ALLOCATED_BYTES = 4096
ROW_METADATA_BYTES = 256
FIXED_DATABASE_BYTES = 65536
KINDS = ("origin", "decision", "result", "approval", "delivery")
ROLE_RECORDS = {"executor": 3, "controller": 2}

_DDL = """
CREATE TABLE frames(ordinal INTEGER PRIMARY KEY,claim BLOB NOT NULL);
CREATE TABLE records(sequence INTEGER PRIMARY KEY,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,
 identity TEXT NOT NULL,payload BLOB NOT NULL,UNIQUE(kind,ordinal),
 FOREIGN KEY(ordinal) REFERENCES frames(ordinal));
CREATE INDEX identity_lookup ON records(kind,identity,ordinal);
CREATE INDEX frame_records ON records(ordinal);
CREATE TABLE meta(value BLOB NOT NULL);
CREATE TABLE totals(id INTEGER PRIMARY KEY CHECK(id=1),frames INTEGER NOT NULL,
 records INTEGER NOT NULL,bytes INTEGER NOT NULL,claim_bytes INTEGER NOT NULL,audit_bytes INTEGER NOT NULL);
INSERT INTO totals VALUES(1,0,0,0,0,0);
CREATE TRIGGER frames_no_update BEFORE UPDATE ON frames BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER frames_no_delete BEFORE DELETE ON frames BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER records_no_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER records_no_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER meta_no_update BEFORE UPDATE ON meta BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER meta_no_delete BEFORE DELETE ON meta BEGIN SELECT RAISE(ABORT,'RETENTION_IMMUTABLE'); END;
CREATE TRIGGER frames_total AFTER INSERT ON frames BEGIN
 UPDATE totals SET frames=frames+1,claim_bytes=claim_bytes+LENGTH(NEW.claim) WHERE id=1; END;
CREATE TRIGGER records_total AFTER INSERT ON records BEGIN
 UPDATE totals SET records=records+1,bytes=bytes+LENGTH(NEW.payload) WHERE id=1; END;
"""


def _schema(conn):
    return conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


@lru_cache(maxsize=1)
def _expected_schema():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_DDL)
        return _schema(conn)
    finally:
        conn.close()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def present(path):
    return path.exists() or path.is_symlink()


def active(root):
    return any(present(Path(root) / name) for name in (AREA, MARKER, AUDIT))


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    _sync(path.parent)


def _read(path, uid, maximum=32768):
    from tools.gold_pilot_install import _read_target

    return _read_target(path, uid=uid, maximum=maximum)


def _binding(original, base):
    from tools.gold_strategy_budget_enrollment import runtime_check
    from live.execution.demo_gold_pilot import config_check
    from live.execution.demo_gold_pilot_enrollment import _request_check, SCOPE_REQUEST_SCHEMA

    runtime_check(base, base["source_commit"])
    config = original["configuration"]
    request = original["request"]
    config_check(config)
    _request_check(request, source_commit=base["source_commit"], account=config["account_ref_sha256"])
    if request["schema"] == SCOPE_REQUEST_SCHEMA:
        require(
            _digest(base) == request["budget_scope"]["scope_enrollment"]["runtime_sha256"],
            "RETENTION_SIGNED_RUNTIME_CHANGED",
        )
    duration = config["ends_at_ms"] - config["started_at_ms"]
    require(duration == 86400000, "RETENTION_WINDOW")
    frames = (duration + CLOSING_MS) // INTERVAL_MS + 2
    # Explicit ceilings, not a claim that every possible 16 MB result fits a
    # whole day. SQLite's payload, row/index/page overhead and independent
    # witnesses have separate budgets. No record is truncated or discarded.
    # Both custody roots share the same claim identity. The physical ceiling
    # covers the larger executor shape; each root enforces its own row limit.
    records_per_frame = max(ROLE_RECORDS.values())
    events_per_frame = records_per_frame + 1
    database = (
        MAX_TOTAL_BYTES
        + frames * (CLAIM_MAX_BYTES + records_per_frame * (PAGE_BYTES + ROW_METADATA_BYTES))
        + FIXED_DATABASE_BYTES
    )
    database = ((database + PAGE_BYTES - 1) // PAGE_BYTES) * PAGE_BYTES
    return dict(
        schema=SCHEMA,
        source_commit=base["source_commit"],
        account_ref_sha256=config["account_ref_sha256"],
        request_sha256=_digest(request),
        configuration_sha256=_digest(config),
        base_runtime=deepcopy(base),
        started_at_ms=config["started_at_ms"],
        ends_at_ms=config["ends_at_ms"],
        closing_ms=CLOSING_MS,
        minimum_interval_ms=INTERVAL_MS,
        max_frames=frames,
        max_record_bytes=MAX_RECORD_BYTES,
        max_total_bytes=MAX_TOTAL_BYTES,
        records_per_frame=records_per_frame,
        role_records_per_frame=dict(ROLE_RECORDS),
        claim_max_bytes=CLAIM_MAX_BYTES,
        event_max_bytes=EVENT_MAX_BYTES,
        page_bytes=PAGE_BYTES,
        max_database_bytes=database,
        # DELETE rollback journals contain old database pages plus headers.
        # Reserve twice the entire bounded DB, even though one transaction
        # inserts only one bounded payload. Existing journals reject on open.
        max_journal_bytes=2 * database + FIXED_DATABASE_BYTES,
        max_audit_events=events_per_frame * frames,
        max_audit_bytes=events_per_frame * frames * EVENT_MAX_BYTES,
        max_audit_allocated_bytes=events_per_frame * frames * EVENT_ALLOCATED_BYTES,
        max_directory_bytes=events_per_frame * frames * 256 + FIXED_DATABASE_BYTES,
        max_metadata_allocated_bytes=4 * 32768 + PAGE_BYTES,
        submission_enabled=False,
        economic_authority=False,
    )


class Retention:
    """One custody root with an exclusive writer or shared reader lease.

    Writers audit all history on cold open. A prechecked object can release its
    lease during external work, then verify append-only additions on acquire.
    Explicit verify_history=False is available only to read-only exact readers:
    they verify selected payload/claim witnesses, never allocate or infer quota.
    Old payload hashes are checked on exact reads; the write audit checks every
    claim, row metadata, immutable event body, counters and file-name set.
    """

    def __init__(self, root, *, original, base_runtime, uid, role, read_only=False, verify_history=True):
        self.root = Path(root).absolute()
        self.uid = uid
        self.role = role
        self.read_only = read_only
        self.conn = None
        self.lock = None
        self._failed = False
        self._known_state = None
        self._events = set()
        require(
            type(read_only) is bool and type(verify_history) is bool and (verify_history or read_only),
            "RETENTION_READER_SCOPE",
        )
        self.verify_history = verify_history
        require(self.root.resolve() == self.root and role in ("executor", "controller"), "RETENTION_ROOT")
        _check_path(self.root, uid, directory=True)
        self.area = self.root / AREA
        self.audit = self.root / AUDIT
        self.marker = self.root / MARKER
        self.binding = _binding(original, base_runtime)
        self.capacity_sha256 = _digest(self.binding)
        self._binding_body = canonical(dict(binding=self.binding, role=role))
        try:
            create = not present(self.marker)
            if create:
                require(not active(self.root) and not read_only, "RETENTION_HISTORY_MISSING")
                legacy = self.root / "strategy-decisions"
                if present(legacy):
                    _check_path(legacy, uid, directory=True)
                    require(next(legacy.iterdir(), None) is None, "RETENTION_LEGACY_REQUIRES_REVIEW")
                self.area.mkdir(mode=0o700)
                self.audit.mkdir(mode=0o700)
                (self.audit / "events").mkdir(mode=0o700)
                _write(self.area / "lease.lock", b"")
                _write(self.area / "records.sqlite3", b"")
            self._lease()
            self._paths()
            path = self.area / "records.sqlite3"
            self._database_inode = self._inode(path)
            uri = path.as_uri() + "?mode=" + ("ro" if read_only else "rw")
            self.conn = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
            if create:
                self.conn.executescript(
                    "PRAGMA page_size=4096; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;" + _DDL
                )
                self.conn.execute("INSERT INTO meta VALUES(?)", (self._binding_body,))
                _write(self.audit / "binding.json", self._binding_body)
                _write(self.marker, self._binding_body)
                _write(self.audit / "head.json", canonical(self._state()))
                _sync(self.area)
                _sync(self.audit)
                _sync(self.root)
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA trusted_schema=OFF")
            self.conn.execute("PRAGMA synchronous=FULL")
            if not read_only:
                pages = self.binding["max_database_bytes"] // PAGE_BYTES
                require(
                    self.conn.execute("PRAGMA max_page_count=" + str(pages)).fetchone()[0] == pages,
                    "RETENTION_DATABASE_PAGE_BUDGET",
                )
            else:
                self.conn.execute("BEGIN")
            self._guards()
            if verify_history:
                self._validate(full=True)
            else:
                self._checkpoint()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _inode(path):
        info = path.lstat()
        return info.st_dev, info.st_ino

    def _lease(self):
        require(self.lock is None, "RETENTION_ALREADY_ACQUIRED")
        path = self.area / "lease.lock"
        _check_path(path, self.uid, directory=False)
        fd = os.open(path, (os.O_RDONLY if self.read_only else os.O_RDWR) | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            mode = fcntl.LOCK_SH if self.read_only else fcntl.LOCK_EX
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == self._inode(path), "RETENTION_LEASE_CHANGED")
            require(
                not hasattr(self, "_lease_inode") or self._lease_inode == self._inode(path), "RETENTION_LEASE_CHANGED"
            )
            self._lease_inode = self._inode(path)
            self.lock = fd
        except BlockingIOError:
            os.close(fd)
            raise ValueError("RETENTION_BUSY") from None
        except BaseException:
            os.close(fd)
            raise

    def _paths(self):
        for path in (self.root, self.area, self.audit, self.audit / "events"):
            _check_path(path, self.uid, directory=True)
        for path in self.area.iterdir():
            require(path.name in ("lease.lock", "records.sqlite3"), "RETENTION_DATABASE_PARTIAL_OR_UNEXPECTED")
            _check_path(path, self.uid, directory=False)
        require(
            {p.name for p in self.area.iterdir()} == {"lease.lock", "records.sqlite3"}, "RETENTION_DATABASE_MISSING"
        )
        # Do not let opening SQLite repair/delete a crash journal before the
        # independently retained event/head disagreement is reviewed.
        for path in self.audit.iterdir():
            require(path.name in ("binding.json", "head.json", "events"), "RETENTION_AUDIT_PARTIAL_OR_UNEXPECTED")

    def _physical(self):
        info = (self.area / "records.sqlite3").lstat()
        require(
            max(info.st_size, info.st_blocks * 512) <= self.binding["max_database_bytes"],
            "RETENTION_DATABASE_PHYSICAL_BUDGET",
        )
        pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
        require(
            self.conn.execute("PRAGMA page_size").fetchone()[0] == PAGE_BYTES
            and pages * PAGE_BYTES <= self.binding["max_database_bytes"],
            "RETENTION_DATABASE_PAGE_BUDGET",
        )
        require(
            sum(max(p.stat().st_size, p.stat().st_blocks * 512) for p in (self.area, self.audit, self.audit / "events"))
            <= self.binding["max_directory_bytes"],
            "RETENTION_DIRECTORY_PHYSICAL_BUDGET",
        )
        metadata = (self.marker, self.audit / "binding.json", self.audit / "head.json", self.area / "lease.lock")
        require(
            sum(max(p.stat().st_size, p.stat().st_blocks * 512) for p in metadata if present(p))
            <= self.binding["max_metadata_allocated_bytes"],
            "RETENTION_METADATA_PHYSICAL_BUDGET",
        )
        journal = self.area / "records.sqlite3-journal"
        if present(journal):
            _check_path(journal, self.uid, directory=False)
            info = journal.stat()
            require(
                max(info.st_size, info.st_blocks * 512) <= self.binding["max_journal_bytes"],
                "RETENTION_JOURNAL_PHYSICAL_BUDGET",
            )

    def _guards(self):
        require(self.lock is not None and not self._failed, "RETENTION_LEASE_REQUIRED")
        self._paths()
        require(self._inode(self.area / "records.sqlite3") == self._database_inode, "RETENTION_DATABASE_CHANGED")
        require(_read(self.marker, self.uid) == self._binding_body, "RETENTION_BINDING_CHANGED")
        require(_read(self.audit / "binding.json", self.uid) == self._binding_body, "RETENTION_BINDING_AUDIT")
        require(
            self.conn.execute("SELECT value FROM meta").fetchall() == [(self._binding_body,)],
            "RETENTION_DATABASE_BINDING",
        )
        require(_schema(self.conn) == _expected_schema(), "RETENTION_DATABASE_SCHEMA_CHANGED")
        require(self.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete", "RETENTION_DATABASE_JOURNAL_MODE")
        self._physical()

    def _state(self):
        require(_digest(self.binding) == self.capacity_sha256, "RETENTION_BINDING_CHANGED")
        rows = self.conn.execute("SELECT id,frames,records,bytes,claim_bytes,audit_bytes FROM totals").fetchall()
        require(
            len(rows) == 1 and rows[0][0] == 1 and all(type(v) is int and v >= 0 for v in rows[0]), "RETENTION_COUNTERS"
        )
        _, frames, records, total, claims, audit = rows[0]
        return dict(
            schema="GOLD_PILOT_RETENTION_HEAD_V1",
            capacity_sha256=self.capacity_sha256,
            frames=frames,
            records=records,
            bytes=total,
            claim_bytes=claims,
            audit_bytes=audit,
            submission_enabled=False,
        )

    def _bounded_state(self, state):
        require(
            state["frames"] <= self.binding["max_frames"]
            and state["records"] <= self.binding["role_records_per_frame"][self.role] * state["frames"],
            "RETENTION_FRAME_BUDGET",
        )
        require(
            state["bytes"] <= self.binding["max_total_bytes"]
            and state["claim_bytes"] <= state["frames"] * CLAIM_MAX_BYTES
            and state["audit_bytes"] <= self.binding["max_audit_bytes"],
            "RETENTION_BYTE_BUDGET",
        )
        require(
            self.conn.execute("SELECT COALESCE(MAX(ordinal),0) FROM frames").fetchone()[0] == state["frames"]
            and self.conn.execute("SELECT COALESCE(MAX(sequence),0) FROM records").fetchone()[0] == state["records"],
            "RETENTION_COUNTER_TAIL",
        )

    def _checkpoint(self):
        state = self._state()
        self._bounded_state(state)
        require(_read(self.audit / "head.json", self.uid) == canonical(state), "RETENTION_HEAD_CHANGED")
        self._known_state = state

    def _check_claim(self, claim):
        require(
            type(claim) is dict
            and set(claim)
            == set("schema ordinal admitted_at_ms bundle_sha256 capacity_sha256 submission_enabled".split()),
            "RETENTION_CLAIM_FIELDS",
        )
        require(
            claim["schema"] == "GOLD_PILOT_OBSERVATION_CLAIM_V1"
            and type(claim["ordinal"]) is int
            and 1 <= claim["ordinal"] <= self.binding["max_frames"]
            and type(claim["admitted_at_ms"]) is int
            and self.binding["started_at_ms"] <= claim["admitted_at_ms"] < self.binding["ends_at_ms"] + CLOSING_MS
            and claim["capacity_sha256"] == self.capacity_sha256
            and claim["submission_enabled"] is False
            and type(claim["bundle_sha256"]) is str
            and re.fullmatch("[a-f0-9]{64}", claim["bundle_sha256"]),
            "RETENTION_CLAIM_BINDING",
        )

    def _event(self, kind, ordinal, identity, size):
        return dict(
            schema="GOLD_PILOT_RETENTION_EVENT_V1",
            kind=kind,
            ordinal=ordinal,
            identity=identity,
            bytes=size,
            capacity_sha256=self.capacity_sha256,
            submission_enabled=False,
        )

    def _witness(self, event):
        body = canonical(event)
        path = self.audit / "events" / (_digest(event) + ".json")
        require(
            len(body) <= EVENT_MAX_BYTES and _read(path, self.uid, EVENT_MAX_BYTES) == body, "RETENTION_AUDIT_CHANGED"
        )
        require(path.stat().st_blocks * 512 <= EVENT_ALLOCATED_BYTES, "RETENTION_AUDIT_PHYSICAL_BUDGET")
        return path.name, len(body)

    def _audit_names(self, expected):
        actual = set()
        with os.scandir(self.audit / "events") as entries:
            for entry in entries:
                require(
                    len(actual) < self.binding["max_audit_events"]
                    and entry.is_file(follow_symlinks=False)
                    and re.fullmatch("[a-f0-9]{64}\\.json", entry.name),
                    "RETENTION_AUDIT_FILE",
                )
                actual.add(entry.name)
        require(actual == expected, "RETENTION_AUDIT_HISTORY_MISSING")

    def _validate(self, *, full=False):
        require(self.lock is not None and not self._failed, "RETENTION_LEASE_REQUIRED")
        prior = None if full else self._known_state
        old_frames = prior["frames"] if prior else 0
        old_records = prior["records"] if prior else 0
        expected = set(self._events) if prior else set()
        frames = old_frames
        records = old_records
        total = prior["bytes"] if prior else 0
        claims = prior["claim_bytes"] if prior else 0
        audit = prior["audit_bytes"] if prior else 0
        last = None
        if old_frames:
            previous = self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (old_frames,)).fetchone()
            require(previous is not None, "RETENTION_FRAME_CHAIN")
            last = decode(previous[0])["admitted_at_ms"]
        for index, body in self.conn.execute(
            "SELECT ordinal,claim FROM frames WHERE ordinal>? ORDER BY ordinal", (old_frames,)
        ):
            frames += 1
            claim = decode(body)
            self._check_claim(claim)
            require(
                index == frames == claim["ordinal"]
                and 0 < len(body) <= CLAIM_MAX_BYTES
                and body == canonical(claim)
                and (last is None or claim["admitted_at_ms"] - last >= INTERVAL_MS),
                "RETENTION_FRAME_CHAIN",
            )
            last = claim["admitted_at_ms"]
            claims += len(body)
            name, size = self._witness(self._event("claim", index, _digest(claim), len(body)))
            expected.add(name)
            audit += size
        allowed = ("origin", "decision", "result") if self.role == "executor" else ("result", "approval", "delivery")
        for sequence, kind, ordinal, identity, size in self.conn.execute(
            "SELECT sequence,kind,ordinal,identity,LENGTH(payload) FROM records WHERE sequence>? ORDER BY sequence",
            (old_records,),
        ):
            records += 1
            require(
                sequence == records
                and kind in allowed
                and 1 <= ordinal <= frames
                and type(identity) is str
                and re.fullmatch("[a-f0-9]{64}", identity)
                and type(size) is int
                and 0 < size <= self.binding["max_record_bytes"],
                "RETENTION_RECORD_FIELDS",
            )
            total += size
            name, length = self._witness(self._event(kind, ordinal, identity, size))
            expected.add(name)
            audit += length
        if full:
            require(not self.conn.execute("PRAGMA foreign_key_check").fetchall(), "RETENTION_RECORD_ORPHAN")
            require(
                not self.conn.execute(
                    "SELECT ordinal FROM records GROUP BY ordinal HAVING COUNT(*)>? LIMIT 1",
                    (self.binding["role_records_per_frame"][self.role],),
                ).fetchone(),
                "RETENTION_FRAME_RECORD_BUDGET",
            )
        state = self._state()
        require(
            (frames, records, total, claims, audit)
            == tuple(state[k] for k in ("frames", "records", "bytes", "claim_bytes", "audit_bytes")),
            "RETENTION_COUNTERS_CHANGED",
        )
        self._bounded_state(state)
        self._audit_names(expected)
        require(_read(self.audit / "head.json", self.uid) == canonical(state), "RETENTION_HEAD_CHANGED")
        self._physical()
        self._known_state = state
        self._events = expected

    def _commit(self, sql, args, event):
        require(not self.read_only and self.lock is not None and not self._failed, "RETENTION_WRITE_LEASE_REQUIRED")
        require(self._state() == self._known_state, "RETENTION_CONCURRENT_CHANGE")
        require(_read(self.audit / "head.json", self.uid) == canonical(self._known_state), "RETENTION_HEAD_CHANGED")
        raw = canonical(event)
        require(len(raw) <= EVENT_MAX_BYTES, "RETENTION_AUDIT_BYTE_BUDGET")
        path = self.audit / "events" / (_digest(event) + ".json")
        self._physical()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(sql, args)
            self.conn.execute("UPDATE totals SET audit_bytes=audit_bytes+? WHERE id=1", (len(raw),))
            new = self._state()
            self._bounded_state(new)
            self._physical()
            require(not present(path), "RETENTION_AUDIT_CONFLICT")
            _write(path, raw)
            require(path.stat().st_blocks * 512 <= EVENT_ALLOCATED_BYTES, "RETENTION_AUDIT_PHYSICAL_BUDGET")
            self.conn.execute("COMMIT")
            # Any interruption remains explicit partial evidence. Never rewind
            # a claim, delete its witness, or manufacture a fresh budget.
            temporary = self.audit / "head.next"
            _write(temporary, canonical(new))
            os.replace(temporary, self.audit / "head.json")
            _sync(self.audit)
            self._physical()
            self._known_state = new
            self._events.add(path.name)
        except BaseException:
            self._failed = True
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def summary(self):
        self._guards()
        if self.verify_history:
            self._validate(full=True)
        else:
            self._checkpoint()
        return dict(self._known_state)

    def metadata(self):
        """Verified current checkpoint, without asserting a new full audit."""
        self._guards()
        state = self._state()
        self._bounded_state(state)
        require(
            state == self._known_state and _read(self.audit / "head.json", self.uid) == canonical(state),
            "RETENTION_HEAD_CHANGED",
        )
        return dict(state)

    def claim(self, *, bundle, now_ms):
        require(self.role == "executor" and not self.read_only, "RETENTION_EXECUTOR_REQUIRED")
        require(
            type(now_ms) is int and self.binding["started_at_ms"] <= now_ms < self.binding["ends_at_ms"] + CLOSING_MS,
            "RETENTION_WINDOW",
        )
        from tools.gold_strategy_budget_enrollment import runtime_check

        runtime_check(bundle, self.binding["source_commit"])
        base = self.binding["base_runtime"]
        ignored = {"episode_id"} if base["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1" else set()
        require(
            {k: v for k, v in bundle.items() if k not in ignored}
            == {k: v for k, v in base.items() if k not in ignored},
            "RETENTION_RUNTIME_CHANGED",
        )
        count = self._known_state["frames"]
        require(count < self.binding["max_frames"], "RETENTION_FRAME_BUDGET")
        if count:
            previous = decode(self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (count,)).fetchone()[0])
            require(now_ms - previous["admitted_at_ms"] >= INTERVAL_MS, "RETENTION_CADENCE")
            require(
                self.conn.execute("SELECT 1 FROM records WHERE kind='result' AND ordinal=?", (count,)).fetchone(),
                "RETENTION_INCOMPLETE_FRAME",
            )
        claim = dict(
            schema="GOLD_PILOT_OBSERVATION_CLAIM_V1",
            ordinal=count + 1,
            admitted_at_ms=now_ms,
            bundle_sha256=_digest(bundle),
            capacity_sha256=self.capacity_sha256,
            submission_enabled=False,
        )
        self.adopt(claim)
        return claim

    def adopt(self, claim):
        require(not self.read_only and self.lock is not None and not self._failed, "RETENTION_WRITE_LEASE_REQUIRED")
        self._check_claim(claim)
        ordinal = claim["ordinal"]
        body = canonical(claim)
        require(len(body) <= CLAIM_MAX_BYTES, "RETENTION_CLAIM_BUDGET")
        current = self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()
        if current:
            require(current == (body,), "RETENTION_CLAIM_CONFLICT")
            self._witness(self._event("claim", ordinal, _digest(claim), len(body)))
            return
        require(ordinal == self._known_state["frames"] + 1, "RETENTION_MISSING_FRAME")
        if ordinal > 1:
            previous = decode(
                self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal - 1,)).fetchone()[0]
            )
            require(claim["admitted_at_ms"] - previous["admitted_at_ms"] >= INTERVAL_MS, "RETENTION_CADENCE")
            require(
                self.conn.execute("SELECT 1 FROM records WHERE kind='result' AND ordinal=?", (ordinal - 1,)).fetchone(),
                "RETENTION_INCOMPLETE_FRAME",
            )
        self._commit(
            "INSERT INTO frames VALUES(?,?)", (ordinal, body), self._event("claim", ordinal, _digest(claim), len(body))
        )

    def put(self, claim, kind, value):
        require(not self.read_only and self.lock is not None, "RETENTION_WRITE_LEASE_REQUIRED")
        self._check_claim(claim)
        require(kind in KINDS, "RETENTION_KIND")
        require(
            kind
            in (("origin", "decision", "result") if self.role == "executor" else ("result", "approval", "delivery")),
            "RETENTION_ROLE",
        )
        ordinal = claim["ordinal"]
        body = canonical(value)
        identity = _digest(value)
        row = self.conn.execute("SELECT payload FROM records WHERE ordinal=? AND kind=?", (ordinal, kind)).fetchone()
        if row:
            require(row == (body,), "RETENTION_RECORD_CONFLICT")
            self.read(kind, identity)
            return identity
        require(
            self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone() == (canonical(claim),),
            "RETENTION_CLAIM_MISSING",
        )
        require(len(body) <= min(MAX_RECORD_BYTES, self.binding["max_record_bytes"]), "RETENTION_RECORD_BUDGET")
        require(self._known_state["bytes"] + len(body) <= self.binding["max_total_bytes"], "RETENTION_BYTE_BUDGET")
        require(
            self.conn.execute("SELECT COUNT(*) FROM records WHERE ordinal=?", (ordinal,)).fetchone()[0]
            < self.binding["role_records_per_frame"][self.role],
            "RETENTION_FRAME_RECORD_BUDGET",
        )
        if kind == "origin":
            require(
                ordinal == self._known_state["frames"]
                and not self.conn.execute("SELECT 1 FROM records WHERE ordinal=?", (ordinal,)).fetchone(),
                "RETENTION_ORIGIN_CAPTURE_ORDER",
            )
            from services.research.demo_execution_origin import validate, GOLD_ACCOUNT_TERMS_SCHEMA

            validate(value)
            require(
                value["schema"] == GOLD_ACCOUNT_TERMS_SCHEMA
                and value["symbol"] == "XAUUSDT"
                and value["status"] in ("CAPTURED", "FAILED")
                and value["account_ref_sha256"] in (None, self.binding["account_ref_sha256"])
                and (value["status"] == "FAILED" or value["account_ref_sha256"] == self.binding["account_ref_sha256"]),
                "RETENTION_ORIGIN_BINDING",
            )
        if kind in ("decision", "result"):
            decision = value if kind == "decision" else value["decision"]
            require(
                decision.get("source_commit") == self.binding["source_commit"]
                and decision.get("account_ref_sha256") == self.binding["account_ref_sha256"]
                and decision.get("bundle_sha256") == claim["bundle_sha256"],
                "RETENTION_RESULT_BINDING",
            )
            if self.role == "executor":
                origin = self.read_claim(claim, "origin")
                if origin is not None:
                    require(
                        decision.get("origin_sha256") == _digest(decode(origin))
                        and decode(origin)["status"] == "CAPTURED",
                        "RETENTION_ORIGIN_HASH",
                    )
            if kind == "result":
                require(value.get("decision_sha256") == _digest(decision), "RETENTION_DECISION_HASH")
                if self.role == "executor":
                    original = self.conn.execute(
                        "SELECT payload FROM records WHERE kind='decision' AND ordinal=?", (ordinal,)
                    ).fetchone()
                    require(original == (canonical(decision),), "RETENTION_DECISION_MISSING")
                    self.read("decision", _digest(decision))
        self._commit(
            "INSERT INTO records(kind,ordinal,identity,payload) VALUES(?,?,?,?)",
            (kind, ordinal, identity, body),
            self._event(kind, ordinal, identity, len(body)),
        )
        return identity

    def _selected(self, kind, identity, *, ordinal=None):
        require(self.lock is not None and not self._failed, "RETENTION_LEASE_REQUIRED")
        require(
            kind in KINDS and type(identity) is str and re.fullmatch("[a-f0-9]{64}", identity), "RETENTION_IDENTITY"
        )
        row = self.conn.execute(
            "SELECT ordinal,payload FROM records WHERE kind=? AND identity=?"
            + (" AND ordinal=?" if ordinal is not None else "")
            + " ORDER BY ordinal DESC LIMIT 1",
            (kind, identity, ordinal) if ordinal is not None else (kind, identity),
        ).fetchone()
        require(row is not None, "RETENTION_RECORD_MISSING")
        ordinal, body = row
        require(
            type(body) is bytes
            and 0 < len(body) <= MAX_RECORD_BYTES
            and _digest(decode(body)) == identity
            and canonical(decode(body)) == body,
            "RETENTION_PAYLOAD_CHANGED",
        )
        self._witness(self._event(kind, ordinal, identity, len(body)))
        claim_row = self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()
        require(claim_row is not None, "RETENTION_CLAIM_MISSING")
        claim = decode(claim_row[0])
        self._check_claim(claim)
        require(claim["ordinal"] == ordinal and claim_row[0] == canonical(claim), "RETENTION_CLAIM_CHANGED")
        self._witness(self._event("claim", ordinal, _digest(claim), len(claim_row[0])))
        return body, claim

    def read(self, kind, identity):
        return self._selected(kind, identity)[0]

    def reference(self, kind, identity):
        return self._selected(kind, identity)[1]

    def read_claim(self, claim, kind):
        """Read one exact frame slot, without creating/adopting its claim.

        None means an audited durable claim has no record in this slot. Missing
        or conflicting claims and witnesses always reject before a caller can
        use the absence to begin an external capture.
        """
        self.metadata()
        self._check_claim(claim)
        require(kind in KINDS, "RETENTION_KIND")
        body = canonical(claim)
        require(
            self.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (claim["ordinal"],)).fetchone() == (body,),
            "RETENTION_CLAIM_MISSING_OR_CHANGED",
        )
        self._witness(self._event("claim", claim["ordinal"], _digest(claim), len(body)))
        row = self.conn.execute(
            "SELECT identity FROM records WHERE ordinal=? AND kind=?", (claim["ordinal"], kind)
        ).fetchone()
        if row is None:
            return None
        raw, reference = self._selected(kind, row[0], ordinal=claim["ordinal"])
        require(reference == claim, "RETENTION_CLAIM_CHANGED")
        return raw

    def release(self):
        if self.conn is not None and self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def acquire(self):
        require(self.conn is not None and not self._failed, "RETENTION_CLOSED_OR_PARTIAL")
        self._lease()
        try:
            if self.read_only:
                self.conn.execute("BEGIN")
            self._guards()
            if self.verify_history:
                self._validate()
            else:
                self._checkpoint()
        except BaseException:
            self.release()
            raise
        return self

    def close(self):
        self.release()
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
