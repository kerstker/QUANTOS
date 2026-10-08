"""Private retention storage engine shared by explicitly bounded adapters.

This module supplies no path admission, operating CLI or maintenance authority.
Adapters must validate all paths before entry and retain their own custody policy.
It keeps the original manifest, no-replace moves, whole-set preflight and lookup
semantics; importing it does not authorize use on production state.
"""

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys


class Storage:
    def __init__(self, io, *, schema):
        self.io = io
        self.schema = schema

    def _prepare(self, root, archive, names, *, evidence):
        info = self.io.directory(root)
        self.io.directory(archive.parent)
        self.io.require(
            root != archive and root not in archive.parents and (archive not in root.parents), "ARCHIVE_LOCATION"
        )
        self.io.require(info.st_dev == archive.parent.stat().st_dev, "SAME_FILESYSTEM_REQUIRED")
        self.io.require(not archive.exists(), "ARCHIVE_EXISTS")
        self.io.require(
            type(names) is list and 0 < len(names) <= 30000 and (len(names) == len(set(names))), "CANDIDATE_SET"
        )
        entries = []
        total = 0
        for name in sorted(names):
            self.io.relative(name)
            self.io.parents(root, name)
            raw = self.io.read(root / name)
            digest = hashlib.sha256(raw).hexdigest()
            self.io.require(Path(name).stem == digest, "RECORD_DIGEST")
            total += len(raw)
            self.io.require(total <= 512 * 1024**2, "TRANSACTION_BUDGET")
            entries.append({"name": name, "sha256": digest, "bytes": len(raw)})
        self.io.latest_protected(root, names)
        manifest = {
            "schema": self.schema,
            "root": str(root),
            "root_identity": [info.st_dev, info.st_ino],
            "entries": entries,
            "evidence": {name: hashlib.sha256(raw).hexdigest() for name, raw in evidence.items()},
        }
        archive.mkdir(mode=0o700)
        self.io.sync(archive.parent)
        records = archive / "records"
        records.mkdir(mode=0o700)
        for name in names:
            path = records
            for part in self.io.relative(name).parent.parts:
                child = path / part
                child.mkdir(mode=0o700, exist_ok=True)
                self.io.sync(path)
                path = child
        self.io.write_new(archive / "lock", b"")
        for name, payload in evidence.items():
            self.io.write_new(archive / name, payload)
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        self.io.write_new(archive / "manifest.json", raw)
        self.io.write_new(archive / "manifest.sha256", hashlib.sha256(raw).hexdigest().encode())
        return {"status": "PREPARED", "files": len(entries), "bytes": total}

    @contextmanager
    def transaction(self, root, archive):
        info = self.io.directory(root)
        self.io.directory(archive)
        self.io.directory(archive / "records")
        self.io.require(
            root != archive and root not in archive.parents and (archive not in root.parents), "ARCHIVE_LOCATION"
        )
        self.io.require(self.io.read(archive / "lock") == b"", "LOCK_CONTENT")
        with os.fdopen(os.open(archive / "lock", os.O_RDONLY | os.O_NOFOLLOW), "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = self.io.read(archive / "manifest.json")
            self.io.require(
                hashlib.sha256(raw).hexdigest().encode() == self.io.read(archive / "manifest.sha256", 64),
                "MANIFEST_DIGEST",
            )
            manifest = load_json_without_duplicate_keys(raw)
            self.io.require(
                manifest["schema"] == self.schema
                and manifest["root"] == str(root)
                and (manifest["root_identity"] == [info.st_dev, info.st_ino]),
                "MANIFEST_BINDING",
            )
            evidence = manifest.get("evidence", {})
            self.io.require(
                type(evidence) is dict and set(evidence) in (set(), {"plan.json", "review.json"}), "EVIDENCE_SET"
            )
            for name, digest in evidence.items():
                self.io.require(hashlib.sha256(self.io.read(archive / name)).hexdigest() == digest, "EVIDENCE_DIGEST")
            entries = manifest["entries"]
            self.io.require(type(entries) is list and 0 < len(entries) <= 30000, "MANIFEST_ENTRIES")
            self.io.require(len({e["name"] for e in entries}) == len(entries), "DUPLICATE_RECORD")
            for entry in entries:
                self.io.relative(entry["name"])
                self.io.parents(root, entry["name"])
                self.io.parents(archive / "records", entry["name"])
                self.io.require(
                    entry["sha256"] == Path(entry["name"]).stem
                    and type(entry["bytes"]) is int
                    and (0 <= entry["bytes"] <= 16 * 1024**2),
                    "MANIFEST_ENTRY",
                )
            yield (root, archive, entries)

    def placements(self, root, archive, entries):
        result = []
        for entry in entries:
            source = root / entry["name"]
            target = archive / "records" / entry["name"]
            present = [p for p in (source, target) if p.exists() or p.is_symlink()]
            self.io.require(len(present) == 1, "PLACEMENT_CONFLICT_OR_MISSING")
            raw = self.io.read(present[0])
            self.io.require(
                len(raw) == entry["bytes"] and hashlib.sha256(raw).hexdigest() == entry["sha256"], "RECORD_CHANGED"
            )
            result.append((source, target, present[0]))
        return result

    def apply(self, root, archive):
        with self.transaction(root, archive) as (root, archive, entries):
            self.io.latest_protected(root, [e["name"] for e in entries])
            located = self.placements(root, archive, entries)
            for source, target, present in located:
                if present == source:
                    self.io.move(source, target)
            self.io.require(all((p == t for _, t, p in self.placements(root, archive, entries))), "MOVE_INCOMPLETE")
            return {"status": "ARCHIVED", "moved": len(entries), "production_ready": False}

    def rollback(self, root, archive):
        with self.transaction(root, archive) as (root, archive, entries):
            located = self.placements(root, archive, entries)
            for source, target, present in reversed(located):
                if present == target:
                    self.io.move(target, source)
            self.io.require(all((p == s for s, _, p in self.placements(root, archive, entries))), "RESTORE_INCOMPLETE")
            return {"status": "RESTORED", "restored": len(entries), "production_ready": False}

    def prepare_reviewed(self, root, archive, *, plan, review, expected_review_sha256):
        """Bind a scoped review to an offline transaction; never upgrade its authority.

        The review may originate on the source host while root is a restored copy, so
        bytes/digests are bound, not the source-host inode of every candidate. This is
        not proof the reference inventory still describes a changing live namespace.
        """
        plan_raw, review_raw = (self.io.read(plan), self.io.read(review))
        self.io.require(hashlib.sha256(review_raw).hexdigest() == expected_review_sha256, "REVIEW_DIGEST")
        inventory = load_json_without_duplicate_keys(plan_raw)
        report = load_json_without_duplicate_keys(review_raw)
        self.io.require(
            report.get("schema") == "DEMO_RETENTION_REFERENCE_REVIEW_V2"
            and report.get("status") == "SCOPED_REVIEW_ONLY"
            and (report.get("candidate_reference_closure_checked") is True)
            and (report.get("all_references_proven") is False)
            and (report.get("apply_available") is False)
            and (report.get("production_changes") is False),
            "SCOPED_REVIEW_REQUIRED",
        )
        self.io.require(report.get("plan_sha256") == hashlib.sha256(plan_raw).hexdigest(), "PLAN_DIGEST")
        self.io.require(
            inventory.get("schema") == "DEMO_RETENTION_REVIEW_PLAN_V1"
            and inventory.get("apply_available") is False
            and (set(inventory["directories"]) == self.io.SCOPES),
            "PLAN_SCOPE",
        )
        names = set()
        total = 0
        for scope, values in inventory["directories"].items():
            self.io.require(type(values["candidates"]) is list and len(values["candidates"]) <= 30000, "CANDIDATE_SET")
            for entry in values["candidates"]:
                self.io.require(
                    type(entry) is dict
                    and type(entry.get("sha256")) is str
                    and (type(entry.get("bytes")) is int)
                    and (0 <= entry["bytes"] <= 16 * 1024**2),
                    "PLAN_ENTRY",
                )
                name = scope + "/" + entry["sha256"] + ".json"
                self.io.relative(name)
                self.io.parents(root, name)
                self.io.require(name not in names, "DUPLICATE_RECORD")
                raw = self.io.read(root / name)
                total += len(raw)
                self.io.require(total <= 512 * 1024**2, "TRANSACTION_BUDGET")
                self.io.require(
                    len(raw) == entry["bytes"] and hashlib.sha256(raw).hexdigest() == entry["sha256"],
                    "CANDIDATE_CHANGED",
                )
                names.add(name)
                self.io.require(len(names) <= 30000, "CANDIDATE_SET")
        pins = report["pinned_candidates"]
        self.io.require(
            type(pins) is list
            and all((type(name) is str for name in pins))
            and (type(report["pinned_files"]) is int)
            and (type(report["candidate_files"]) is int)
            and (len(pins) == len(set(pins)) == report["pinned_files"])
            and (set(pins) <= names)
            and (report["candidate_files"] == len(names)),
            "REVIEW_CANDIDATE_SET",
        )
        selected = sorted(names - set(pins))
        if not selected:
            return {
                "status": "NO_ELIGIBLE_RECORDS",
                "files": 0,
                "bytes": 0,
                "pinned_files": len(pins),
                "all_references_proven": False,
                "production_ready": False,
            }
        result = self._prepare(root, archive, selected, evidence={"plan.json": plan_raw, "review.json": review_raw})
        return result | {"pinned_files": len(pins), "all_references_proven": False, "production_ready": False}

    def lookup(self, root, archive, name):
        """Read an original record across archived/restored placement, with digest checks.

        Returns original bytes, not current-state or execution authority. Unknown
        records are not silently replaced with another archive or latest pointer.
        """
        self.io.relative(name)
        with self.transaction(root, archive) as (root, archive, entries):
            selected = [entry for entry in entries if entry["name"] == name]
            if selected:
                _, _, path = self.placements(root, archive, selected)[0]
            else:
                self.io.parents(root, name)
                path = root / name
            raw = self.io.read(path)
            self.io.require(hashlib.sha256(raw).hexdigest() == Path(name).stem, "RECORD_CHANGED")
            return raw

    def lookup_many(self, root, archives, name):
        """Read across an explicit bounded set of offline archive transactions.

        Every supplied manifest is checked while all transaction locks are held.
        The caller supplies the active archive set; this does not discover omitted
        batches, establish catalogue completeness, or grant production access.
        """
        self.io.directory(root)
        self.io.relative(name)
        self.io.require(type(archives) is list and len(archives) <= 32, "ARCHIVE_SET")
        self.io.require(len(archives) == len(set(archives)), "ARCHIVE_SET")
        with ExitStack() as locks:
            matches = []
            total = 0
            for archive in sorted(archives):
                _, _, entries = locks.enter_context(self.transaction(root, archive))
                total += len(entries)
                self.io.require(total <= 100000, "ARCHIVE_LOOKUP_BUDGET")
                matches.extend(((archive, entry) for entry in entries if entry["name"] == name))
            self.io.require(len(matches) <= 1, "ARCHIVE_RECORD_AMBIGUOUS")
            if matches:
                archive, entry = matches[0]
                _, _, path = self.placements(root, archive, [entry])[0]
            else:
                self.io.parents(root, name)
                path = root / name
            raw = self.io.read(path)
            self.io.require(hashlib.sha256(raw).hexdigest() == Path(name).stem, "RECORD_CHANGED")
            return raw
