"""One-off maintenance: purge the 60 legacy h_*/hx_* demo/UI-fixture rows that leaked into the
real data/cache/experiment_registry.db before the 2026-07-09 file-collision fix (see
tools/crypto_research_offline.py's docstring + tests/governance/test_experiment_registry_
isolation.py). Those rows are structurally unrelated to the real carry_composite/crowd_contra
candidates (universe='' price-technical demo factors on an 18-coin CoinGecko dataset, vs
universe='cs_majors' Binance funding/OI weekly factors) and were never in competition with them
for selection — but AlphaCatalog.global_trial_count()/catalog_var_sr() count every registered
alpha, so their presence corrupts the Deflated-Sharpe multiple-testing correction for the two real
alphas (confirmed: contaminated DSR=0.6087/REQUIRES_HUMAN_REVIEW vs honest DSR=1.0000 once removed,
top-tier gap audit 2026-07-09).

Fail-closed safety: before deleting anything, recomputes each legacy row's definition_hash (the
same identity AlphaCatalog.register_trial uses) and refuses to proceed if any legacy hash collides
with a real (non-h_/hx_) alpha's hash — a collision would mean the "unrelated" assumption is wrong
and this script must not touch that trial. Dry-run by default; requires --apply to write. Always
back up the db file yourself before running with --apply (this script does not do it for you).

**IMPORTANT — this is a ONE-OFF tool, not a safe-to-repeat maintenance sweep.** Its h_/hx_ prefix
match is a genuine legacy-demo-data heuristic ONLY because, as of 2026-07-09, the sole historical
writer of h_/hx_-prefixed alpha_ids into data/cache/experiment_registry.db was tools/
crypto_research_offline.py's now-fixed-forward demo regenerator (confirmed by inspecting the real
db before this script's first run). The h_/hx_ naming convention itself is NOT unique to that demo
path — it is also the generic hypothesis-naming scheme used by services/intelligence/
{evolutionary_search,search_space}.py (the real GuidedSearchLoop/EvolutionarySearchLoop machinery)
and tools/crypto_history_research.py, none of which persist to this file TODAY (they build an
in-memory AlphaCatalog with no ExperimentRegistry/SqliteExperimentStore at all — confirmed by grep).
If a FUTURE change ever routes that real search machinery's trials into the production
experiment_registry.db (e.g. the roadmap item "route out-of-gate search trials into the persistent
trial count"), re-running this script would incorrectly delete real trials sharing the same prefix
convention. Before ever re-running this script, re-verify (as this docstring's authors did) which
real writers currently touch the target db and confirm none of them use h_/hx_ ids.

The tool is permanently retired for any database that has migrated to the
append-only search-provenance schema. Such a database has authority trials that
must never be deleted; cleanup had to complete before its first provenance write.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Tuple

from quantos.contracts.alpha import AlphaDefinition

_DEFAULT_DB = os.path.join("data", "cache", "experiment_registry.db")
_LEGACY_PREFIXES = ("h_", "hx_")


def _is_legacy(alpha_id: str) -> bool:
    return alpha_id.startswith(_LEGACY_PREFIXES)


def plan_purge(db_path: str) -> Tuple[list, list, set]:
    """Returns (legacy_alpha_ids, real_alpha_ids, legacy_only_trial_hashes) — pure given a db
    path, no mutation. Raises ValueError (fail-closed) on:
      - a hash collision between a legacy and a real alpha_state definition (see module
        docstring), or
      - any ORPHAN trial hash — a `trials` row with NO corresponding alpha_state row at all
        (legacy or real). CatalogSnapshot's own docstring calls `trials` a first-class SUPERSET
        of the alphas' own definition hashes (register_trial() is a public API that can record a
        trial with no alpha ever registered for it, e.g. an out-of-gate search candidate) — if
        any such orphan exists, this script can no longer prove a legacy-classified hash isn't
        ALSO independently backed by a real orphan trial sharing the same definition content (the
        `trials` table dedups by hash with no provenance/multiplicity, so two different sources
        registering the identical config are indistinguishable from one row). Fail closed rather
        than silently risking an undercounted real trial (2026-07-09 review finding)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "search_attempt_events" in tables or "search_candidates" in tables:
            raise ValueError(
                "refusing to purge: database has append-only search provenance; "
                "the legacy cleanup tool is retired after this migration"
            )
        rows = conn.execute("SELECT alpha_id, definition_json FROM alpha_state").fetchall()
        all_trial_hashes = {r["config_hash"] for r in conn.execute("SELECT config_hash FROM trials")}
    finally:
        conn.close()

    legacy_ids, real_ids = [], []
    legacy_hashes, real_hashes = set(), set()
    for r in rows:
        defn = AlphaDefinition.model_validate(json.loads(r["definition_json"]))
        if _is_legacy(r["alpha_id"]):
            legacy_ids.append(r["alpha_id"])
            legacy_hashes.add(defn.definition_hash)
        else:
            real_ids.append(r["alpha_id"])
            real_hashes.add(defn.definition_hash)

    collision = legacy_hashes & real_hashes
    if collision:
        raise ValueError(
            f"refusing to purge: {len(collision)} definition_hash(es) shared between a legacy "
            f"(h_/hx_) row and a real row — the 'structurally unrelated' assumption this script "
            f"relies on does not hold for this db, needs manual review: {collision}")

    orphans = all_trial_hashes - legacy_hashes - real_hashes
    if orphans:
        raise ValueError(
            f"refusing to purge: {len(orphans)} trial hash(es) in the `trials` table have NO "
            f"corresponding alpha_state row at all (legacy or real) — this db has orphan trials, "
            f"so a legacy-classified hash can no longer be proven not to ALSO be independently "
            f"backed by a real orphan trial sharing the same definition content; needs manual "
            f"review: {orphans}")

    return sorted(legacy_ids), sorted(real_ids), legacy_hashes - real_hashes


def apply_purge(db_path: str, legacy_ids: list) -> dict:
    """Deletes legacy alpha_state/evaluations rows and any trial hash exclusively used by a
    legacy definition. Real rows/hashes (even if never actually referenced) are never touched."""
    if not legacy_ids:
        return {"alpha_state_deleted": 0, "evaluations_deleted": 0, "trials_deleted": 0}
    _, _, legacy_only_hashes = plan_purge(db_path)   # recompute inside the same call for safety

    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in legacy_ids)
        cur = conn.execute(
            f"DELETE FROM alpha_state WHERE alpha_id IN ({placeholders})", legacy_ids)
        alpha_state_deleted = cur.rowcount
        cur = conn.execute(
            f"DELETE FROM evaluations WHERE alpha_id IN ({placeholders})", legacy_ids)
        evaluations_deleted = cur.rowcount
        trials_deleted = 0
        if legacy_only_hashes:
            hash_placeholders = ",".join("?" for _ in legacy_only_hashes)
            cur = conn.execute(
                f"DELETE FROM trials WHERE config_hash IN ({hash_placeholders})",
                list(legacy_only_hashes))
            trials_deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"alpha_state_deleted": alpha_state_deleted,
            "evaluations_deleted": evaluations_deleted, "trials_deleted": trials_deleted}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=_DEFAULT_DB)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run, prints the plan only)")
    args = ap.parse_args()

    legacy_ids, real_ids, legacy_only_hashes = plan_purge(args.db)
    print(f"db: {args.db}")
    print(f"legacy (h_/hx_) rows found: {len(legacy_ids)}")
    print(f"real rows found (kept, never touched): {len(real_ids)} -> {real_ids}")
    print(f"trial hashes exclusively used by legacy rows: {len(legacy_only_hashes)}")

    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to purge (back up the db first).")
        return

    result = apply_purge(args.db, legacy_ids)
    print(f"\nAPPLIED: {result}")


if __name__ == "__main__":
    main()
