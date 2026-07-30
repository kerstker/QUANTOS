"""Offline, time-explicit top-tier capability assessment.

The registry declares targets only. Current status is derived from named,
read-only evidence checkers so editing a score file cannot raise maturity.
Results are deterministic for a fixed assessment context.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import tomllib
from typing import Any, Callable

from pydantic import ValidationError

from quantos.contracts.hashing import source_file_sha256, stable_hash
from quantos.contracts.model_risk import (
    ChallengerReview,
    ChallengerVerdict,
    ModelCard,
    OfflineEvaluation,
    PromptTemplateCard,
    RedTeamReport,
    RedTeamVerdict,
    ReviewIdentityAssurance,
    RuntimeInvocation,
    RuntimeInvocationOutcome,
    ToolManifest,
)
from services.governance.model_risk_dataset import (
    DatasetBundleError,
    verify_pit_bundle,
)
from services.governance.external_model_provider_evidence import (
    ExternalModelProviderEvidenceError,
    verify_external_model_provider_evidence,
)
from services.governance.external_sigstore_evidence import (
    ExternalSigstoreEvidenceError,
    verify_external_sigstore_evidence,
)
from services.governance.multi_asset_research_evidence import (
    MultiAssetResearchEvidenceError,
    verify_multi_asset_research_evidence,
)
from services.governance.artifact_admission import (
    load_json_without_duplicate_keys,
)
from services.governance.artifact_admission_store import (
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionStore,
    ArtifactAdmissionStoreError,
)
from services.governance.model_risk_history import (
    ModelRiskHistoryError,
    history_matches_evidence,
    validate_model_risk_history,
)
from services.governance.l2_restore_checkpoint import (
    L2RestoreCheckpointError,
    L2RestoreHistoryHead,
    TrustedL2RestoreCheckpointKey,
    load_signed_l2_restore_checkpoint_json,
    verify_signed_l2_restore_checkpoint,
)
from services.governance.l2_restore_history import (
    L2_RESTORE_HISTORY_STORE_KIND,
    L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
    L2RestoreHistoryStore,
    L2RestoreHistoryStoreError,
    RestoreCadencePolicy,
    evaluate_cadence,
)
from services.governance.reproducibility_evidence import (
    ContainerReproducibilityEvidenceError,
    verify_container_reproducibility_evidence,
)
from services.governance.vulnerability_evidence import (
    ActionsAuditEvidenceError,
    ContainerAuditEvidenceError,
    NpmAuditEvidenceError,
    PythonAuditEvidenceError,
    verify_actions_audit_evidence,
    verify_container_audit_evidence,
    verify_npm_audit_evidence,
    verify_python_audit_evidence,
)


DOMAINS = {
    "data_moat",
    "alpha_research",
    "validation_model_risk",
    "portfolio_construction",
    "risk_management",
    "execution_microstructure",
    "ai_agents",
    "governance_security",
    "platform_engineering",
    "reliability_operations",
    "frontend_console",
}
TIERS = {"A", "B"}
MATURITIES = {f"M{i}" for i in range(6)}
CLASSIFICATIONS = {"L2_SAFE_NOW", "NEEDS_EXTERNAL", "POST_L2"}
EVIDENCE_TYPES = {
    "implementation",
    "verification",
    "data",
    "operation",
    "governance",
    "reproducibility",
}
STATUSES = {"PASS", "PARTIAL", "FAILED", "UNVERIFIED", "MANUAL_EVIDENCE_REQUIRED"}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class AssessmentContext:
    as_of_ms: int


def _paths_exist(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    paths = args.get("paths", [])
    if not isinstance(paths, list) or not paths:
        return _result("UNVERIFIED", "M0", [], "checker has no evidence paths")
    missing = [path for path in paths if not (repo_root / path).exists()]
    if missing:
        return _result("FAILED", "M0", paths, "missing: " + ", ".join(missing))
    maturity = args.get("pass_maturity", target)
    return _result("PASS", maturity, paths, "all declared evidence paths exist")


def _multi_asset_research_evidence(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    evidence_path = args.get("evidence_path")
    bundle_path = args.get("bundle_path")
    manifest_path = args.get("manifest_path")
    implementation_paths = args.get("implementation_paths")
    verification_paths = args.get("verification_paths")
    declared = [evidence_path, bundle_path, manifest_path]
    if (
        any(not isinstance(value, str) or not value for value in declared)
        or not isinstance(implementation_paths, list)
        or not implementation_paths
        or any(not isinstance(value, str) or not value for value in implementation_paths)
        or not isinstance(verification_paths, list)
        or not verification_paths
        or any(not isinstance(value, str) or not value for value in verification_paths)
    ):
        return _result(
            "UNVERIFIED",
            "M0",
            [value for value in declared if isinstance(value, str)],
            "multi-asset checker contract is invalid",
        )
    evidence = [*declared, *implementation_paths, *verification_paths]
    try:
        document = verify_multi_asset_research_evidence(
            evidence_path,
            repo_root=repo_root,
            bundle_path=bundle_path,
            manifest_path=manifest_path,
            implementation_paths=tuple(implementation_paths),
            verification_paths=tuple(verification_paths),
        )
    except (MultiAssetResearchEvidenceError, OSError, TypeError, ValueError) as exc:
        return _result(
            "FAILED",
            "M0",
            evidence,
            f"multi-asset research evidence invalid: {exc}",
        )
    dataset = document["dataset"]
    results = document["factor_results"]
    maturity = args.get("pass_maturity", target)
    return _result(
        "PASS",
        maturity,
        evidence,
        (
            f"independently replayed {len(results)} FX factors across 3 holding "
            f"periods from {dataset['row_count']} versioned PIT rows / "
            f"{dataset['instrument_count']} currencies; bundle, code, tests, "
            "PIT identity, result digest, and L2 diagnostic-only boundary verified"
        ),
    )


def _text_contains_all(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    needles = args.get("needles", [])
    if not isinstance(path, str) or not isinstance(needles, list) or not needles:
        return _result("UNVERIFIED", "M0", [], "checker arguments are incomplete")
    evidence = repo_root / path
    if not evidence.is_file():
        return _result("FAILED", "M0", [path], f"missing: {path}")
    text = evidence.read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    if missing:
        return _result("FAILED", "M1", [path], "missing required markers: " + ", ".join(missing))
    maturity = args.get("pass_maturity", target)
    return _result("PASS", maturity, [path], "all required markers are present")


def _manual_evidence(
    _repo_root: Path,
    _args: dict[str, Any],
    _target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    return _result("MANUAL_EVIDENCE_REQUIRED", "M1", [], "requires external or human-verified evidence")


def _dotted_ast_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _function_ast_calls(tree: ast.AST, function_name: str) -> set[str] | None:
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return None
    calls: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _dotted_ast_name(node.func)
            if name:
                calls.add(name)
    return calls


def _function_ast_call_lines(
    tree: ast.AST, function_name: str
) -> dict[str, int] | None:
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return None
    lines: dict[str, int] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_ast_name(node.func)
        if name:
            lines[name] = min(lines.get(name, node.lineno), node.lineno)
    return lines


def _calls_with_keywords(tree: ast.AST, call_name: str) -> list[set[str]]:
    matches: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_ast_name(node.func)
        if name == call_name or (name is not None and name.endswith(f".{call_name}")):
            matches.append({keyword.arg for keyword in node.keywords if keyword.arg})
    return matches


def _alpha_trial_record_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _alpha_trial_search_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").upper()


def _ast_function(
    tree: ast.AST,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _ast_call_name(call: ast.Call) -> str:
    return _dotted_ast_name(call.func) or ""


def _ast_calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and (
            _ast_call_name(candidate) == name
            or _ast_call_name(candidate).endswith(f".{name}")
        )
    ]


def _ast_call_argument(
    call: ast.Call,
    position: int,
    keyword_name: str,
) -> ast.AST | None:
    if len(call.args) > position:
        return call.args[position]
    return next(
        (
            keyword.value
            for keyword in call.keywords
            if keyword.arg == keyword_name
        ),
        None,
    )


def _ast_function_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    return {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }


def _ast_name_is(node: ast.AST | None, name: str) -> bool:
    return node is not None and _dotted_ast_name(node) == name


def _ast_mentions_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(candidate, ast.Name) and candidate.id == name
        for candidate in ast.walk(node)
    )


def _owned_transaction_binding(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.With, str, str] | None:
    transaction_calls = _ast_calls_named(function, "_immediate_transaction")
    matches: list[tuple[ast.With, ast.withitem]] = []
    for with_node in (
        node for node in ast.walk(function) if isinstance(node, ast.With)
    ):
        for item in with_node.items:
            if (
                isinstance(item.context_expr, ast.Call)
                and _ast_call_name(item.context_expr).endswith(
                    "._immediate_transaction"
                )
            ):
                matches.append((with_node, item))
    if len(transaction_calls) != 1 or len(matches) != 1:
        return None
    with_node, item = matches[0]
    target = item.optional_vars
    if not isinstance(target, (ast.Tuple, ast.List)) or len(target.elts) != 2:
        return None
    connection_name = _dotted_ast_name(target.elts[0])
    lease_name = _dotted_ast_name(target.elts[1])
    if (
        not connection_name
        or not lease_name
        or connection_name == lease_name
        or "." in connection_name
        or "." in lease_name
    ):
        return None
    return with_node, connection_name, lease_name


def _sql_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and _ast_call_name(call).rsplit(".", maxsplit=1)[-1]
        in {"execute", "executemany", "executescript"}
    ]


def _transaction_control_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and _ast_call_name(call).rsplit(".", maxsplit=1)[-1]
        in {
            "commit",
            "rollback",
            "_commit_owned_transaction",
            "_rollback_owned_transaction",
        }
    ]


def _call_source(source: str, call: ast.Call) -> str:
    return (ast.get_source_segment(source, call) or "").upper()


def _has_sql_transaction_control(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
) -> bool:
    return any(
        any(marker in _call_source(source, call) for marker in ("BEGIN ", "COMMIT", "ROLLBACK"))
        for call in _sql_calls(function)
    )


def _owned_terminal_control_calls(
    tree: ast.AST,
    transaction: ast.FunctionDef | ast.AsyncFunctionDef,
    connection_name: str,
    action: str,
) -> list[ast.Call] | None:
    direct = [
        call
        for call in ast.walk(transaction)
        if isinstance(call, ast.Call)
        and _ast_call_name(call) == f"{connection_name}.{action}"
    ]
    delegated_name = f"_{action}_owned_transaction"
    delegated = _ast_calls_named(transaction, delegated_name)
    if not direct and not delegated:
        return None
    if any(
        not _ast_name_is(_ast_call_argument(call, 0, "conn"), connection_name)
        for call in delegated
    ):
        return None
    if delegated:
        owner = _ast_function(tree, delegated_name)
        if owner is None or "conn" not in _ast_function_parameters(owner):
            return None
        terminal_calls = [
            candidate
            for candidate in ast.walk(owner)
            if isinstance(candidate, ast.Call)
            and _ast_call_name(candidate) == f"conn.{action}"
        ]
        if len(terminal_calls) != 1 or len(_transaction_control_calls(owner)) != 1:
            return None
    return [*direct, *delegated]


def _alpha_trial_transaction_semantic_failures(
    store_tree: ast.AST,
    store_source: str,
) -> list[str]:
    """Verify the reviewed wrapper -> transaction -> locked-helper call graph.

    SQL is intentionally inspected only inside the store-owned transaction and
    locked primitive. Public wrappers are allowed to be thin and therefore do
    not need to repeat transaction or SQL literals.
    """

    failures: list[str] = []
    immediate = _ast_function(store_tree, "_immediate_transaction")
    require_lock = _ast_function(store_tree, "_require_write_lock")
    register = _ast_function(store_tree, "register_search_candidates")
    wrapper = _ast_function(store_tree, "insert_evaluation")
    helper = _ast_function(store_tree, "_insert_evaluation_locked")

    if immediate is None:
        failures.append("store_path:_immediate_transaction:missing")
    else:
        yields = [
            node for node in ast.walk(immediate) if isinstance(node, ast.Yield)
        ]
        yield_value = yields[0].value if len(yields) == 1 else None
        valid_yield = (
            isinstance(yield_value, ast.Tuple)
            and len(yield_value.elts) == 2
            and _dotted_ast_name(yield_value.elts[0]) is not None
            and isinstance(yield_value.elts[1], ast.Name)
        )
        connection_name = (
            _dotted_ast_name(yield_value.elts[0]) if valid_yield else ""
        )
        lease_name = (
            yield_value.elts[1].id
            if valid_yield and isinstance(yield_value.elts[1], ast.Name)
            else ""
        )
        connection_assignments = [
            assignment
            for assignment in ast.walk(immediate)
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == connection_name
                for target in (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
            )
            and _ast_name_is(assignment.value, "self._conn")
        ]
        connection_assignment = connection_name == "self._conn" or any(
            assignment.lineno < yields[0].lineno
            for assignment in connection_assignments
        )
        lease_assignments = [
            assignment
            for assignment in ast.walk(immediate)
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == lease_name
                for target in (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
            )
            and isinstance(assignment.value, ast.Call)
            and _ast_call_name(assignment.value).endswith("._WriteLease")
        ]
        if not lease_assignments:
            lease_assignments = [
                assignment
                for assignment in ast.walk(immediate)
                if isinstance(assignment, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == lease_name
                    for target in (
                        assignment.targets
                        if isinstance(assignment, ast.Assign)
                        else [assignment.target]
                    )
                )
                and isinstance(assignment.value, ast.Call)
                and _ast_call_name(assignment.value) == "_WriteLease"
            ]
        lease_call = lease_assignments[0].value if len(lease_assignments) == 1 else None
        lease_generation = (
            _ast_call_argument(lease_call, 1, "generation")
            if isinstance(lease_call, ast.Call)
            else None
        )
        lease_owner_thread = (
            _ast_call_argument(lease_call, 2, "owner_thread")
            if isinstance(lease_call, ast.Call)
            else None
        )
        valid_lease_creation = (
            isinstance(lease_call, ast.Call)
            and _ast_name_is(
                _ast_call_argument(lease_call, 0, "connection"), connection_name
            )
            and isinstance(lease_generation, ast.Attribute)
            and (_dotted_ast_name(lease_generation) or "").startswith("self.")
            and "generation" in (_dotted_ast_name(lease_generation) or "").lower()
            and isinstance(lease_owner_thread, ast.Call)
            and _ast_call_name(lease_owner_thread).endswith("get_ident")
            and lease_assignments[0].lineno < yields[0].lineno
        )
        active_lease_assignments = [
            assignment
            for assignment in ast.walk(immediate)
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and any(
                (target_name := _dotted_ast_name(target)) is not None
                and target_name.startswith("self.")
                and "lease" in target_name.lower()
                for target in (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
            )
            and _ast_name_is(assignment.value, lease_name)
            and assignment.lineno < yields[0].lineno
        ]
        lease_cleanup = any(
            isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and any(
                (target_name := _dotted_ast_name(target)) is not None
                and target_name.startswith("self.")
                and "lease" in target_name.lower()
                for target in (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
            )
            and isinstance(assignment.value, ast.Constant)
            and assignment.value.value is None
            and assignment.lineno > yields[0].lineno
            for assignment in ast.walk(immediate)
        )
        begin_calls = [
            call
            for call in _sql_calls(immediate)
            if _ast_call_name(call) == f"{connection_name}.execute"
            and "BEGIN IMMEDIATE" in _call_source(store_source, call)
        ]
        commit_calls = (
            None
            if not connection_name
            else _owned_terminal_control_calls(
                store_tree,
                immediate,
                connection_name,
                "commit",
            )
        )
        rollback_calls = (
            None
            if not connection_name
            else _owned_terminal_control_calls(
                store_tree,
                immediate,
                connection_name,
                "rollback",
            )
        )
        ownership_guards = _ast_calls_named(
            immediate, "_reject_write_ownership_conflict"
        )
        if (
            len(begin_calls) != 1
            or not valid_yield
            or not connection_assignment
            or not valid_lease_creation
            or len(active_lease_assignments) != 1
            or not lease_cleanup
            or len(ownership_guards) != 1
            or (
                begin_calls
                and ownership_guards
                and ownership_guards[0].lineno >= begin_calls[0].lineno
            )
            or commit_calls is None
            or len(commit_calls) != 1
            or rollback_calls is None
            or (commit_calls and commit_calls[0].lineno <= yields[0].lineno)
            or (
                rollback_calls
                and not any(call.lineno > yields[0].lineno for call in rollback_calls)
            )
        ):
            failures.append("store_path:_immediate_transaction:owned_transaction")

    if require_lock is None:
        failures.append("store_path:_require_write_lock:missing")
    else:
        parameters = _ast_function_parameters(require_lock)
        comparison_names = {
            _dotted_ast_name(candidate)
            for comparison in ast.walk(require_lock)
            if isinstance(comparison, ast.Compare)
            for candidate in (comparison.left, *comparison.comparators)
        }
        referenced_names = {
            _dotted_ast_name(candidate)
            for candidate in ast.walk(require_lock)
            if isinstance(candidate, (ast.Name, ast.Attribute))
        }
        has_raise = any(
            isinstance(node, ast.Raise) for node in ast.walk(require_lock)
        )
        has_active_lease_check = any(
            isinstance(name, str)
            and name.startswith("self.")
            and "lease" in name.lower()
            for name in comparison_names
        )
        has_generation_check = any(
            isinstance(name, str)
            and name.startswith("self.")
            and "generation" in name.lower()
            for name in comparison_names
        )
        has_lease_type_check = any(
            _ast_name_is(_ast_call_argument(call, 0, "object"), "lease")
            and _ast_name_is(_ast_call_argument(call, 1, "class_or_tuple"), "_WriteLease")
            for call in _ast_calls_named(require_lock, "isinstance")
        )
        has_thread_check = any(
            _ast_call_name(call).endswith("get_ident")
            for call in ast.walk(require_lock)
            if isinstance(call, ast.Call)
        )
        if (
            not {"conn", "lease"} <= parameters
            or not {
                "conn",
                "self._conn",
                "lease",
                "lease.connection",
                "lease.generation",
                "lease.owner_thread",
            }
            <= comparison_names
            or not has_active_lease_check
            or not has_generation_check
            or not has_lease_type_check
            or not has_thread_check
            or "conn.in_transaction" not in referenced_names
            or not has_raise
        ):
            failures.append(
                "store_path:_require_write_lock:connection_lease_binding"
            )

    wrapper_binding = None if wrapper is None else _owned_transaction_binding(wrapper)
    if wrapper is None or wrapper_binding is None:
        failures.append("store_path:insert_evaluation:owned_transaction")
    else:
        with_node, connection_name, lease_name = wrapper_binding
        helper_calls = _ast_calls_named(wrapper, "_insert_evaluation_locked")
        helper_calls_in_transaction = _ast_calls_named(
            with_node, "_insert_evaluation_locked"
        )
        if len(helper_calls) != 1 or helper_calls != helper_calls_in_transaction:
            failures.append(
                "store_path:insert_evaluation:locked_helper_delegation"
            )
        else:
            helper_call = helper_calls[0]
            connection_argument = _ast_call_argument(helper_call, 0, "conn")
            lease_argument = _ast_call_argument(helper_call, 1, "lease")
            if (
                not _ast_name_is(connection_argument, connection_name)
                or not _ast_name_is(lease_argument, lease_name)
            ):
                failures.append(
                    "store_path:insert_evaluation:same_connection_and_lease"
                )
        if _sql_calls(wrapper):
            failures.append("store_path:insert_evaluation:wrapper_bypass_locked_helper")
        if _transaction_control_calls(wrapper) or _has_sql_transaction_control(
            wrapper, store_source
        ):
            failures.append("store_path:insert_evaluation:split_commit")

    if helper is None:
        failures.append("store_path:_insert_evaluation_locked:missing")
    else:
        parameters = _ast_function_parameters(helper)
        lock_calls = _ast_calls_named(helper, "_require_write_lock")
        sql_calls = _sql_calls(helper)
        valid_lock_call = False
        if len(lock_calls) == 1:
            connection_argument = _ast_call_argument(lock_calls[0], 0, "conn")
            lease_argument = _ast_call_argument(lock_calls[0], 1, "lease")
            valid_lock_call = (
                _ast_name_is(connection_argument, "conn")
                and _ast_name_is(lease_argument, "lease")
                and (
                    not sql_calls
                    or lock_calls[0].lineno < min(call.lineno for call in sql_calls)
                )
            )
        if not {"conn", "lease"} <= parameters or not valid_lock_call:
            failures.append(
                "store_path:_insert_evaluation_locked:valid_lease_guard"
            )
        if any(
            not _ast_call_name(call).startswith("conn.") for call in sql_calls
        ):
            failures.append(
                "store_path:_insert_evaluation_locked:same_connection_sql"
            )
        if _transaction_control_calls(helper) or _has_sql_transaction_control(
            helper, store_source
        ):
            failures.append("store_path:_insert_evaluation_locked:split_commit")

        marker_calls = {
            marker: [
                call
                for call in sql_calls
                if marker in _call_source(store_source, call)
            ]
            for marker in (
                "SELECT CONFIG_HASH FROM SEARCH_CANDIDATES",
                "INSERT INTO EVALUATIONS",
                "INSERT INTO SEARCH_EVALUATIONS",
                "INSERT INTO EVALUATION_AUDIT_OUTBOX",
            )
        }
        candidate_calls = marker_calls[
            "SELECT CONFIG_HASH FROM SEARCH_CANDIDATES"
        ]
        candidate_names: set[str] = set()
        for assignment in ast.walk(helper):
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                continue
            value = assignment.value
            if value is None or not any(
                candidate in set(ast.walk(value)) for candidate in candidate_calls
            ):
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            candidate_names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
        candidate_rejected = any(
            any(_ast_mentions_name(if_node.test, name) for name in candidate_names)
            and any(isinstance(node, ast.Raise) for node in ast.walk(if_node))
            for if_node in ast.walk(helper)
            if isinstance(if_node, ast.If)
        )
        if not candidate_calls or not candidate_names or not candidate_rejected:
            failures.append(
                "store_path:_insert_evaluation_locked:registered_candidate_binding"
            )
        if any(
            not marker_calls[marker]
            for marker in (
                "INSERT INTO EVALUATIONS",
                "INSERT INTO SEARCH_EVALUATIONS",
                "INSERT INTO EVALUATION_AUDIT_OUTBOX",
            )
        ):
            failures.append(
                "store_path:_insert_evaluation_locked:atomic_evaluation_link_outbox"
            )

    register_binding = None if register is None else _owned_transaction_binding(register)
    if register is None or register_binding is None:
        failures.append(
            "store_path:register_search_candidates:transactional_trial_registration"
        )
    else:
        with_node, connection_name, _lease_name = register_binding
        sql_calls = _sql_calls(register)
        with_sql_calls = set(_sql_calls(with_node))
        required_write_calls = {
            marker: [
                call
                for call in sql_calls
                if marker in _call_source(store_source, call)
            ]
            for marker in (
                "INSERT INTO SEARCH_CANDIDATES",
                "INSERT OR IGNORE INTO TRIALS",
            )
        }
        if (
            any(not calls for calls in required_write_calls.values())
            or any(
                call not in with_sql_calls
                or not _ast_call_name(call).startswith(f"{connection_name}.")
                for calls in required_write_calls.values()
                for call in calls
            )
            or _transaction_control_calls(register)
            or _has_sql_transaction_control(register, store_source)
        ):
            failures.append(
                "store_path:register_search_candidates:transactional_trial_registration"
            )

    return list(dict.fromkeys(failures))


def _alpha_trial_ledger_evidence(
    repo_root: Path,
    args: dict[str, Any],
    _target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path_fields = (
        "evidence_path",
        "ledger_path",
        "snapshot_path",
        "seal_path",
        "entrypoint_path",
        "session_path",
        "factory_path",
        "factory_registry_helper_path",
        "registry_path",
        "persistence_port_path",
        "store_path",
    )
    paths = {field: args.get(field) for field in path_fields}
    verification_paths = args.get("verification_paths", [])
    official_factory_paths = args.get("official_factory_paths", [])
    if (
        any(not isinstance(value, str) or not value for value in paths.values())
        or not isinstance(verification_paths, list)
        or any(not isinstance(value, str) or not value for value in verification_paths)
        or not isinstance(official_factory_paths, list)
        or len(official_factory_paths) != 5
        or any(not isinstance(value, str) or not value for value in official_factory_paths)
        or len(set(official_factory_paths)) != len(official_factory_paths)
    ):
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            "alpha trial-ledger checker arguments are incomplete",
        )
    required_evaluation_verification_paths = {
        "tests/research/test_experiment_store.py",
        "tests/research/test_experiment_store_locked_primitives.py",
        "tests/intelligence/test_registered_candidate_batch.py",
    }
    missing_evaluation_verification = sorted(
        required_evaluation_verification_paths - set(verification_paths)
    )
    if missing_evaluation_verification:
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            "evaluation-safe persistence verification is incomplete: "
            + ", ".join(missing_evaluation_verification),
        )
    declared_paths = [*paths.values(), *verification_paths, *official_factory_paths]
    if any(
        Path(value).is_absolute() or ".." in Path(value).parts
        for value in declared_paths
    ):
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            "alpha trial-ledger paths must stay within the repository",
        )

    implementation_keys = (
        "entrypoint_path",
        "session_path",
        "factory_path",
        "factory_registry_helper_path",
        "registry_path",
        "persistence_port_path",
        "store_path",
    )
    implementation_paths = [paths[key] for key in implementation_keys]
    evidence = [
        *implementation_paths,
        *official_factory_paths,
        *verification_paths,
    ]
    missing_implementation = [
        relative
        for relative in [
            *implementation_paths,
            *official_factory_paths,
            *verification_paths,
        ]
        if not (repo_root / relative).is_file()
    ]
    if missing_implementation:
        return _result(
            "FAILED",
            "M0",
            evidence,
            "missing alpha trial-ledger implementation: "
            + ", ".join(missing_implementation),
        )

    try:
        trees = {
            key: ast.parse((repo_root / paths[key]).read_text(encoding="utf-8"))
            for key in implementation_keys
        }
        factory_tool_trees = {
            relative: ast.parse((repo_root / relative).read_text(encoding="utf-8"))
            for relative in official_factory_paths
        }
        store_source = (repo_root / paths["store_path"]).read_text(encoding="utf-8")
        ast.parse(store_source)
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _result(
            "FAILED",
            "M1",
            evidence,
            f"alpha trial-ledger implementation cannot be parsed: {exc}",
        )

    required_calls = {
        "entrypoint_path": {
            "run_research_session": {"_run_research_session_core"},
        },
        "session_path": {
            "run_research_session": {
                "registry.load_for_evaluation",
                "registry.record_evaluation",
                "registry.save_evaluation_context",
            },
            "_record_report": {"registry.record_evaluation"},
            "_run_evolutionary": {"registry.save_evaluation_context"},
            "_run_mcts": {"registry.save_evaluation_context"},
        },
        "factory_path": {
            "run_alpha_factory": {
                "search_registry.load_for_evaluation",
                "search_registry.begin_search_attempt",
                "_run_alpha_factory_impl",
                "search_registry.save_evaluation_context",
                "search_registry.finish_search_attempt",
            },
            "_run_alpha_factory_impl": {
                "candidate_recorder",
                "_score_expr",
                "executor.map",
            },
        },
        "factory_registry_helper_path": {
            "production_experiment_registry": {
                "SqliteExperimentStore",
                "store.trial_count",
                "ExperimentRegistry",
                "store.close",
            },
        },
        "registry_path": {
            "begin_search_attempt": {"self._store.begin_search_attempt"},
            "register_search_candidates": {
                "self._store.register_search_candidates"
            },
            "finish_search_attempt": {"self._store.finish_search_attempt"},
            "search_evaluations": {"self._store.search_evaluations"},
            "search_history_valid": {"self._store.search_history_valid"},
            "save_evaluation_context": {
                "self._store.save_evaluation_catalog_snapshot"
            },
            "load_for_evaluation": {
                "self._store.all_trial_hashes",
                "self._store.all_alphas",
                "catalog.restore_evaluation_context",
            },
            "record_evaluation": {"self._store.insert_evaluation"},
            "verify_evaluation": {"self._store.get_evaluation", "stable_hash"},
        },
        "persistence_port_path": {
            "save_evaluation_catalog_snapshot": set(),
        },
        "store_path": {
            "save_evaluation_catalog_snapshot": set(),
        },
    }
    wiring_failures: list[str] = []
    for source_key, functions in required_calls.items():
        tree = trees[source_key]
        for function_name, expected_calls in functions.items():
            observed = _function_ast_calls(tree, function_name)
            if observed is None:
                wiring_failures.append(f"{source_key}:{function_name}:missing")
                continue
            missing_calls = sorted(expected_calls - observed)
            if missing_calls:
                wiring_failures.append(
                    f"{source_key}:{function_name}:" + ",".join(missing_calls)
                )
    required_store_markers = (
        "INSERT OR IGNORE INTO trials",
        "ImmutableArtifactError",
        "content_hash",
        "CREATE TABLE IF NOT EXISTS search_evaluations",
        "UNIQUE(search_id, run_id)",
        "_search_evaluation_hash",
    )
    wiring_failures.extend(
        f"store_path:{marker}"
        for marker in required_store_markers
        if marker not in store_source
    )
    evaluation_insert_calls = _calls_with_keywords(
        trees["registry_path"],
        "self._store.insert_evaluation",
    )
    if not any(
        {"search_id", "config_hash"} <= keywords
        for keywords in evaluation_insert_calls
    ):
        wiring_failures.append(
            "registry_path:record_evaluation:exact_search_candidate_binding"
        )
    factory_lines = _function_ast_call_lines(
        trees["factory_path"], "_run_alpha_factory_impl"
    )
    if factory_lines is None:
        wiring_failures.append("factory_path:_run_alpha_factory_impl:missing")
    else:
        recorder_line = factory_lines.get("candidate_recorder")
        score_lines = [
            line
            for name, line in factory_lines.items()
            if name in {"_score_expr", "executor.map"}
        ]
        if recorder_line is None or not score_lines or recorder_line >= min(score_lines):
            wiring_failures.append(
                "factory_path:_run_alpha_factory_impl:candidate_recorder_before_score"
            )
    helper_tree = trees["factory_registry_helper_path"]
    helper_source = (
        repo_root / paths["factory_registry_helper_path"]
    ).read_text(encoding="utf-8")
    helper_function = next(
        (
            node
            for node in ast.walk(helper_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "production_experiment_registry"
        ),
        None,
    )
    helper_failures: list[str] = []
    production_assignment = next(
        (
            node
            for node in ast.walk(helper_tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name)
                and target.id == "PRODUCTION_EXPERIMENT_DB"
                for target in (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
            )
        ),
        None,
    )
    if production_assignment is None:
        helper_failures.append("production_db_constant")
    else:
        value = production_assignment.value
        path_literals = [
            item.value
            for item in ast.walk(value)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        normalized = "/".join(path_literals).replace("\\", "/")
        if "data/cache/experiment_registry.db" not in normalized:
            helper_failures.append("production_db_path")
    store_calls = [
        node
        for node in (ast.walk(helper_function) if helper_function is not None else ())
        if isinstance(node, ast.Call)
        and _dotted_ast_name(node.func) == "SqliteExperimentStore"
    ]
    if len(store_calls) != 1:
        helper_failures.append("sqlite_store_construction")
    else:
        store_call = store_calls[0]
        create_keyword = next(
            (keyword for keyword in store_call.keywords if keyword.arg == "create"),
            None,
        )
        if (
            not store_call.args
            or not isinstance(store_call.args[0], ast.Name)
            or store_call.args[0].id != "PRODUCTION_EXPERIMENT_DB"
            or create_keyword is None
            or not isinstance(create_keyword.value, ast.Constant)
            or create_keyword.value.value is not False
        ):
            helper_failures.append("create_false_fixed_store")
        missing_file_guard = False
        if helper_function is not None:
            for node in ast.walk(helper_function):
                if not isinstance(node, ast.If) or node.lineno >= store_call.lineno:
                    continue
                file_calls = [
                    candidate
                    for candidate in ast.walk(node.test)
                    if isinstance(candidate, ast.Call)
                    and _dotted_ast_name(candidate.func) == "os.path.isfile"
                ]
                guarded_path = any(
                    len(candidate.args) == 1
                    and isinstance(candidate.args[0], ast.Name)
                    and candidate.args[0].id == "PRODUCTION_EXPERIMENT_DB"
                    for candidate in file_calls
                )
                negated = isinstance(node.test, ast.UnaryOp) and isinstance(
                    node.test.op, ast.Not
                )
                raises = any(
                    isinstance(candidate, ast.Raise)
                    for statement in node.body
                    for candidate in ast.walk(statement)
                )
                if guarded_path and negated and raises:
                    missing_file_guard = True
                    break
        if not missing_file_guard:
            helper_failures.append("missing_file_guard_before_connect")
    if helper_function is None or not any(
        isinstance(node, (ast.Yield, ast.YieldFrom))
        for node in ast.walk(helper_function)
    ):
        helper_failures.append("registry_yield")
    final_close = False
    if helper_function is not None:
        for try_node in (
            node for node in ast.walk(helper_function) if isinstance(node, ast.Try)
        ):
            if any(
                isinstance(node, ast.Call)
                and _dotted_ast_name(node.func) == "store.close"
                for statement in try_node.finalbody
                for node in ast.walk(statement)
            ):
                final_close = True
                break
    if not final_close:
        helper_failures.append("finally_close")
    if "@contextmanager" not in helper_source:
        helper_failures.append("contextmanager")
    if helper_failures:
        wiring_failures.append(
            "factory registry helper:" + ",".join(helper_failures)
        )
    store_tree = trees["store_path"]
    search_evaluation_hash_function = next(
        (
            node
            for node in ast.walk(store_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_search_evaluation_hash"
        ),
        None,
    )
    search_evaluation_hash_source = store_source
    if search_evaluation_hash_function is None:
        semantics_module = (
            "services.research.experiment_registry.family_schema_v2_semantics"
        )
        imports_canonical_semantics = any(
            isinstance(node, ast.ImportFrom)
            and node.module == semantics_module
            and any(
                alias.name == "search_evaluation_hash"
                and alias.asname == "_search_evaluation_hash"
                for alias in node.names
            )
            for node in ast.walk(store_tree)
        )
        if imports_canonical_semantics:
            semantics_relative = semantics_module.replace(".", "/") + ".py"
            semantics_path = repo_root / semantics_relative
            try:
                search_evaluation_hash_source = semantics_path.read_text(
                    encoding="utf-8"
                )
                semantics_tree = ast.parse(search_evaluation_hash_source)
            except (OSError, UnicodeError, SyntaxError):
                search_evaluation_hash_function = None
            else:
                search_evaluation_hash_function = next(
                    (
                        node
                        for node in ast.walk(semantics_tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "search_evaluation_hash"
                    ),
                    None,
                )
                if semantics_relative not in evidence:
                    evidence.append(semantics_relative)
    search_evaluation_hash_segment = (
        ""
        if search_evaluation_hash_function is None
        else ast.get_source_segment(
            search_evaluation_hash_source,
            search_evaluation_hash_function,
        )
        or ""
    )
    if (
        search_evaluation_hash_function is None
        or "config_hash"
        not in {
            argument.arg
            for argument in (
                *search_evaluation_hash_function.args.posonlyargs,
                *search_evaluation_hash_function.args.args,
                *search_evaluation_hash_function.args.kwonlyargs,
            )
        }
        or '"config_hash": config_hash' not in search_evaluation_hash_segment
    ):
        wiring_failures.append(
            "store_path:_search_evaluation_hash:config_hash_binding"
        )
    wiring_failures.extend(
        _alpha_trial_transaction_semantic_failures(store_tree, store_source)
    )
    if wiring_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "official alpha trial-ledger persistence wiring failed: "
            + "; ".join(wiring_failures),
        )

    required_factory_keywords = {
        "search_registry",
        "search_id",
        "search_kind",
        "search_source",
        "dataset_version",
        "timestamp",
    }
    factory_tool_failures: list[str] = []
    for relative, tree in factory_tool_trees.items():
        run_calls = _calls_with_keywords(tree, "run_alpha_factory")
        helper_calls = {
            name
            for name in ("production_experiment_registry", "make_search_id")
            if _calls_with_keywords(tree, name)
        }
        helper_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "tools.search_trial_registry"
            for alias in node.names
        }
        if not run_calls:
            factory_tool_failures.append(f"{relative}:run_alpha_factory")
            continue
        if not any(required_factory_keywords <= keywords for keywords in run_calls):
            factory_tool_failures.append(f"{relative}:search_identity")
        if helper_calls != {"production_experiment_registry", "make_search_id"}:
            factory_tool_failures.append(f"{relative}:persistent_registry_helper")
        if not {
            "production_experiment_registry",
            "make_search_id",
        } <= helper_imports:
            factory_tool_failures.append(f"{relative}:registry_helper_import")
    if factory_tool_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "factory tool coverage incomplete: "
            + ", ".join(factory_tool_failures),
        )

    evidence_path = repo_root / paths["evidence_path"]
    if not evidence_path.is_file():
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "official guided/evolutionary/GP/MCTS and factory persistence wiring is verified, "
            "but versioned append-only trial-ledger history is not retained",
        )
    evidence.append(paths["evidence_path"])

    try:
        document = load_json_without_duplicate_keys(
            evidence_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return _result(
            "FAILED",
            "M2",
            evidence,
            f"invalid alpha trial-ledger history: {exc}",
        )
    if not isinstance(document, dict):
        return _result(
            "FAILED",
            "M2",
            evidence,
            "alpha trial-ledger history must be an object",
        )
    required_document = {
        "schema_version": 2,
        "mode": "ALPHA_TRIAL_LEDGER_APPEND_ONLY_HISTORY",
        "ledger_contract_version": 2,
        "ledger_path": paths["ledger_path"],
    }
    history_failures = [
        key for key, value in required_document.items() if document.get(key) != value
    ]
    if set(document) != {*required_document, "records"}:
        history_failures.append("document_fields")
    records = document.get("records")
    if not isinstance(records, list):
        history_failures.append("records")
        records = []
    if history_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "alpha trial-ledger history contract failed: "
            + ", ".join(sorted(set(history_failures))),
        )
    if len(records) < 2:
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "trial-ledger history is valid but does not yet prove persistence across two sessions",
        )

    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    config_hash_pattern = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{64})$")
    previous_record_hash = ""
    previous_timestamp = -1
    previous_trials: set[str] = set()
    previous_evaluations: dict[str, tuple[Any, ...]] = {}
    session_ids: set[str] = set()
    history_sessions: dict[str, dict[str, Any]] = {}
    final_trials: list[str] = []
    final_evaluations: list[dict[str, Any]] = []
    record_fields = {
        "sequence",
        "session_id",
        "recorded_at_ms",
        "session_provenance",
        "trials",
        "evaluations",
        "previous_record_hash",
        "record_hash",
    }
    evaluation_fields = {
        "run_id",
        "alpha_id",
        "dataset_version",
        "recorded_ts",
        "content_hash",
    }
    session_fields = {
        "search_kind",
        "source",
        "dataset_version",
        "started_at",
        "completed_at",
        "terminal_event_hash",
        "candidates",
        "evaluation_links",
    }
    session_evaluation_fields = {
        "config_hash",
        "run_id",
        "recorded_at",
        "content_hash",
        "link_hash",
    }
    for expected_sequence, record in enumerate(records, start=1):
        if not isinstance(record, dict) or set(record) != record_fields:
            history_failures.append(f"record_{expected_sequence}_fields")
            continue
        sequence = record.get("sequence")
        session_id = record.get("session_id")
        recorded_at_ms = record.get("recorded_at_ms")
        trials = record.get("trials")
        evaluations = record.get("evaluations")
        provenance = record.get("session_provenance")
        if type(sequence) is not int or sequence != expected_sequence:
            history_failures.append(f"record_{expected_sequence}_sequence")
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in session_ids
        ):
            history_failures.append(f"record_{expected_sequence}_session_id")
        else:
            session_ids.add(session_id)
        if (
            type(recorded_at_ms) is not int
            or recorded_at_ms < 0
            or recorded_at_ms <= previous_timestamp
        ):
            history_failures.append(f"record_{expected_sequence}_recorded_at_ms")
        else:
            previous_timestamp = recorded_at_ms
        session_candidates: list[str] = []
        session_links: list[dict[str, Any]] = []
        linked_runs: set[str] = set()
        if not isinstance(provenance, dict) or set(provenance) != session_fields:
            history_failures.append(f"record_{expected_sequence}_session_provenance")
            provenance = {}
        else:
            for field in ("search_kind", "source", "dataset_version"):
                value = provenance.get(field)
                if not isinstance(value, str) or not value or value != value.strip():
                    history_failures.append(
                        f"record_{expected_sequence}_session_{field}"
                    )
            started_at = provenance.get("started_at")
            completed_at = provenance.get("completed_at")
            if (
                type(started_at) is not int
                or type(completed_at) is not int
                or started_at < 0
                or completed_at < started_at
                or type(recorded_at_ms) is not int
                or completed_at > recorded_at_ms
            ):
                history_failures.append(
                    f"record_{expected_sequence}_session_clock"
                )
            elif (
                expected_sequence > 1
                and started_at <= records[expected_sequence - 2].get("recorded_at_ms", -1)
            ):
                history_failures.append(
                    f"record_{expected_sequence}_session_precedes_prior_snapshot"
                )
            terminal_hash = provenance.get("terminal_event_hash")
            if (
                not isinstance(terminal_hash, str)
                or digest_pattern.fullmatch(terminal_hash) is None
            ):
                history_failures.append(
                    f"record_{expected_sequence}_terminal_event_hash"
                )
            candidates = provenance.get("candidates")
            if (
                not isinstance(candidates, list)
                or not candidates
                or candidates != sorted(set(candidates))
                or any(
                    not isinstance(value, str)
                    or config_hash_pattern.fullmatch(value) is None
                    for value in candidates
                )
            ):
                history_failures.append(
                    f"record_{expected_sequence}_session_candidates"
                )
            else:
                session_candidates = candidates
            links = provenance.get("evaluation_links")
            if not isinstance(links, list) or not links:
                history_failures.append(
                    f"record_{expected_sequence}_session_evaluation_links"
                )
            else:
                for link in links:
                    if (
                        not isinstance(link, dict)
                        or set(link) != session_evaluation_fields
                    ):
                        history_failures.append(
                            f"record_{expected_sequence}_session_evaluation_link"
                        )
                        continue
                    run_id = link.get("run_id")
                    link_recorded_at = link.get("recorded_at")
                    if (
                        not isinstance(link.get("config_hash"), str)
                        or config_hash_pattern.fullmatch(link["config_hash"]) is None
                        or link["config_hash"] not in session_candidates
                        or not isinstance(run_id, str)
                        or not run_id
                        or run_id in linked_runs
                        or type(link_recorded_at) is not int
                        or type(started_at) is not int
                        or type(completed_at) is not int
                        or link_recorded_at < started_at
                        or link_recorded_at > completed_at
                        or not isinstance(link.get("content_hash"), str)
                        or digest_pattern.fullmatch(link["content_hash"]) is None
                        or not isinstance(link.get("link_hash"), str)
                        or digest_pattern.fullmatch(link["link_hash"]) is None
                    ):
                        history_failures.append(
                            f"record_{expected_sequence}_session_evaluation_link"
                        )
                        continue
                    linked_runs.add(run_id)
                    session_links.append(link)
                if [item["run_id"] for item in session_links] != sorted(linked_runs):
                    history_failures.append(
                        f"record_{expected_sequence}_session_link_order"
                    )
        if record.get("previous_record_hash") != previous_record_hash:
            history_failures.append(f"record_{expected_sequence}_previous_record_hash")
        expected_record_hash = _alpha_trial_record_hash(record)
        if record.get("record_hash") != expected_record_hash:
            history_failures.append(f"record_{expected_sequence}_record_hash")
        previous_record_hash = expected_record_hash

        if (
            not isinstance(trials, list)
            or trials != sorted(set(trials))
            or any(
                not isinstance(value, str)
                or config_hash_pattern.fullmatch(value) is None
                for value in trials
            )
        ):
            history_failures.append(f"record_{expected_sequence}_trials")
            trials = []
        current_trials = set(trials)
        if not set(session_candidates) <= current_trials:
            history_failures.append(
                f"record_{expected_sequence}_session_candidate_snapshot"
            )
        if not previous_trials <= current_trials:
            history_failures.append(f"record_{expected_sequence}_trial_rollback")
        previous_trials = current_trials

        current_evaluations: dict[str, tuple[Any, ...]] = {}
        if not isinstance(evaluations, list):
            history_failures.append(f"record_{expected_sequence}_evaluations")
            evaluations = []
        for item in evaluations:
            if not isinstance(item, dict) or set(item) != evaluation_fields:
                history_failures.append(f"record_{expected_sequence}_evaluation_fields")
                continue
            run_id = item.get("run_id")
            alpha_id = item.get("alpha_id")
            dataset_version = item.get("dataset_version")
            recorded_ts = item.get("recorded_ts")
            content_hash = item.get("content_hash")
            if (
                not isinstance(run_id, str)
                or not run_id
                or run_id in current_evaluations
                or not isinstance(alpha_id, str)
                or not alpha_id
                or not isinstance(dataset_version, str)
                or not dataset_version
                or type(recorded_ts) is not int
                or recorded_ts < 0
                or not isinstance(content_hash, str)
                or digest_pattern.fullmatch(content_hash) is None
            ):
                history_failures.append(f"record_{expected_sequence}_evaluation")
                continue
            current_evaluations[run_id] = (
                alpha_id,
                dataset_version,
                recorded_ts,
                content_hash,
            )
        if any(
            current_evaluations.get(run_id) != value
            for run_id, value in previous_evaluations.items()
        ):
            history_failures.append(f"record_{expected_sequence}_evaluation_rollback")
        for link in session_links:
            identity = current_evaluations.get(link["run_id"])
            if (
                identity is None
                or identity[1] != provenance.get("dataset_version")
                or identity[3] != link["content_hash"]
            ):
                history_failures.append(
                    f"record_{expected_sequence}_session_evaluation_snapshot"
                )
        previous_evaluations = current_evaluations
        if isinstance(session_id, str) and provenance:
            history_sessions[session_id] = provenance
        final_trials = trials
        final_evaluations = evaluations
    if not final_trials or not final_evaluations:
        history_failures.append("empty_current_ledger")
    if history_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "alpha trial-ledger history failed: "
            + ", ".join(sorted(set(history_failures))),
        )

    snapshot_path = repo_root / paths["snapshot_path"]
    seal_path = repo_root / paths["seal_path"]
    if not snapshot_path.is_file() or not seal_path.is_file():
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "versioned trial-ledger history is valid, but portable snapshot evidence is unavailable",
        )
    evidence.extend([paths["snapshot_path"], paths["seal_path"]])

    seal_failures: list[str] = []
    try:
        seal = load_json_without_duplicate_keys(seal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return _result(
            "FAILED",
            "M2",
            evidence,
            f"alpha trial-ledger portable seal failed: {exc}",
        )
    seal_fields = {
        "schema_version",
        "mode",
        "ledger_path",
        "history_path",
        "snapshot_path",
        "history_sha256",
        "snapshot_sha256",
        "history_head_hash",
        "record_count",
        "trial_count",
        "evaluation_count",
        "sealed_at_ms",
    }
    if not isinstance(seal, dict) or set(seal) != seal_fields:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "alpha trial-ledger portable seal failed: seal_fields",
        )
    expected_seal_header = {
        "schema_version": 1,
        "mode": "ALPHA_TRIAL_LEDGER_PORTABLE_SEAL",
        "ledger_path": paths["ledger_path"],
        "history_path": paths["evidence_path"],
        "snapshot_path": paths["snapshot_path"],
    }
    if any(seal.get(key) != value for key, value in expected_seal_header.items()):
        seal_failures.append("seal_header")
    for key in ("history_sha256", "snapshot_sha256", "history_head_hash"):
        if (
            not isinstance(seal.get(key), str)
            or digest_pattern.fullmatch(seal[key]) is None
        ):
            seal_failures.append(key)
    for key in ("record_count", "trial_count", "evaluation_count", "sealed_at_ms"):
        if type(seal.get(key)) is not int or seal[key] < 0:
            seal_failures.append(key)
    if not seal_failures:
        try:
            history_sha256 = _sha256_file(evidence_path)
            snapshot_sha256 = _sha256_file(snapshot_path)
        except OSError as exc:
            return _result(
                "FAILED",
                "M2",
                evidence,
                f"alpha trial-ledger portable seal failed: {exc}",
            )
        if history_sha256 != seal["history_sha256"]:
            seal_failures.append("history_sha256")
        if snapshot_sha256 != seal["snapshot_sha256"]:
            seal_failures.append("snapshot_sha256")
        expected_counts = {
            "history_head_hash": previous_record_hash,
            "record_count": len(records),
            "trial_count": len(final_trials),
            "evaluation_count": len(final_evaluations),
        }
        if any(seal[key] != value for key, value in expected_counts.items()):
            seal_failures.append("seal_counts_or_head")
        if seal["sealed_at_ms"] < records[-1]["recorded_at_ms"]:
            seal_failures.append("sealed_at_ms")
    if seal_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "alpha trial-ledger portable seal failed: "
            + ", ".join(sorted(set(seal_failures))),
        )

    ledger_path = snapshot_path

    try:
        connection = sqlite3.connect(
            ledger_path.resolve().as_uri() + "?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {row["name"] for row in table_rows}
        trigger_rows = connection.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        triggers = {
            row["name"]: (row["tbl_name"], row["sql"])
            for row in trigger_rows
        }
        live_trials = sorted(
            row["config_hash"]
            for row in connection.execute(
                "SELECT config_hash FROM trials ORDER BY config_hash"
            ).fetchall()
        )
        evaluation_rows = connection.execute(
            "SELECT run_id,alpha_id,dataset_version,recorded_ts,content_hash,evaluation_json "
            "FROM evaluations ORDER BY run_id"
        ).fetchall()
        search_event_rows = connection.execute(
            "SELECT event_seq,search_id,search_kind,source,dataset_version,status,"
            "recorded_at,prev_event_hash,event_hash "
            "FROM search_attempt_events ORDER BY event_seq"
        ).fetchall()
        search_candidate_rows = connection.execute(
            "SELECT candidate_seq,search_id,config_hash,status,registered_at,"
            "prev_candidate_hash,candidate_hash "
            "FROM search_candidates ORDER BY candidate_seq"
        ).fetchall()
        search_evaluation_rows = connection.execute(
            "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
            "evaluation_content_hash,"
            "prev_link_hash,link_hash FROM search_evaluations ORDER BY link_seq"
        ).fetchall()
    except (OSError, sqlite3.DatabaseError) as exc:
        return _result(
            "FAILED",
            "M2",
            evidence,
            f"portable alpha trial ledger cannot be verified: {exc}",
        )
    finally:
        if "connection" in locals():
            connection.close()

    ledger_failures: list[str] = []
    if integrity != "ok":
        ledger_failures.append("sqlite_integrity")
    if schema_version != 2:
        ledger_failures.append("ledger_schema_version")
    if tables != {
        "sqlite_sequence",
        "trials",
        "evaluations",
        "search_attempt_events",
        "search_candidates",
        "search_evaluations",
    }:
        ledger_failures.append("ledger_tables")
    expected_triggers = {
        f"{table}_no_{operation.lower()}": (table, operation)
        for table in (
            "trials",
            "evaluations",
            "search_attempt_events",
            "search_candidates",
            "search_evaluations",
        )
        for operation in ("UPDATE", "DELETE")
    }
    if set(triggers) != set(expected_triggers):
        ledger_failures.append("append_only_trigger_names")
    for name, (table, operation) in expected_triggers.items():
        actual = triggers.get(name)
        expected_sql = (
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        if (
            actual is None
            or actual[0] != table
            or not isinstance(actual[1], str)
            or _canonical_sql(actual[1]) != _canonical_sql(expected_sql)
        ):
            ledger_failures.append(f"{name}_trigger")
    if live_trials != final_trials:
        ledger_failures.append("current_trials")

    live_evaluations: list[dict[str, Any]] = []
    for row in evaluation_rows:
        try:
            evaluation_payload = load_json_without_duplicate_keys(row["evaluation_json"])
            recomputed = stable_hash(
                {
                    "evaluation": evaluation_payload,
                    "dataset_version": row["dataset_version"],
                }
            )
        except (TypeError, ValueError):
            ledger_failures.append(f"evaluation_json:{row['run_id']}")
            continue
        if recomputed != row["content_hash"]:
            ledger_failures.append(f"evaluation_content_hash:{row['run_id']}")
        live_evaluations.append(
            {
                "run_id": row["run_id"],
                "alpha_id": row["alpha_id"],
                "dataset_version": row["dataset_version"],
                "recorded_ts": row["recorded_ts"],
                "content_hash": row["content_hash"],
            }
        )
    if live_evaluations != final_evaluations:
        ledger_failures.append("current_evaluations")

    evaluations_by_run = {item["run_id"]: item for item in live_evaluations}
    live_sessions: dict[str, dict[str, Any]] = {}
    event_head = ""
    search_start_high_water = -1
    for expected_sequence, row in enumerate(search_event_rows, start=1):
        expected_hash = _alpha_trial_search_hash(
            {
                "dataset_version": row["dataset_version"],
                "prev_event_hash": row["prev_event_hash"],
                "recorded_at": row["recorded_at"],
                "search_id": row["search_id"],
                "search_kind": row["search_kind"],
                "source": row["source"],
                "status": row["status"],
            }
        )
        if (
            row["event_seq"] != expected_sequence
            or row["prev_event_hash"] != event_head
            or row["event_hash"] != expected_hash
            or row["status"] not in {"STARTED", "FAILED", "COMPLETED"}
        ):
            ledger_failures.append(f"search_event:{row['event_seq']}")
        search_id = row["search_id"]
        metadata = (
            row["search_kind"],
            row["source"],
            row["dataset_version"],
        )
        attempt = live_sessions.get(search_id)
        if row["status"] == "STARTED":
            if attempt is not None or row["recorded_at"] < search_start_high_water:
                ledger_failures.append(f"search_started:{search_id}")
            else:
                live_sessions[search_id] = {
                    "search_kind": metadata[0],
                    "source": metadata[1],
                    "dataset_version": metadata[2],
                    "status": "STARTED",
                    "started_at": row["recorded_at"],
                    "completed_at": None,
                    "terminal_event_hash": None,
                    "candidates": [],
                    "evaluation_links": [],
                }
                search_start_high_water = row["recorded_at"]
        elif (
            attempt is None
            or attempt["status"] != "STARTED"
            or metadata
            != (
                attempt["search_kind"],
                attempt["source"],
                attempt["dataset_version"],
            )
            or row["recorded_at"] < attempt["started_at"]
        ):
            ledger_failures.append(f"search_terminal:{search_id}")
        else:
            attempt["status"] = row["status"]
            attempt["completed_at"] = row["recorded_at"]
            attempt["terminal_event_hash"] = row["event_hash"]
        event_head = row["event_hash"]

    candidate_head = ""
    candidate_identities: set[tuple[str, str]] = set()
    latest_candidate_times: dict[str, int] = {}
    live_trial_set = set(live_trials)
    for expected_sequence, row in enumerate(search_candidate_rows, start=1):
        attempt = live_sessions.get(row["search_id"])
        identity = (row["search_id"], row["config_hash"])
        latest_at = latest_candidate_times.get(row["search_id"], -1)
        terminal_at = None if attempt is None else attempt["completed_at"]
        expected_hash = _alpha_trial_search_hash(
            {
                "config_hash": row["config_hash"],
                "prev_candidate_hash": row["prev_candidate_hash"],
                "registered_at": row["registered_at"],
                "search_id": row["search_id"],
                "status": row["status"],
            }
        )
        if (
            row["candidate_seq"] != expected_sequence
            or attempt is None
            or identity in candidate_identities
            or row["status"] != "REGISTERED"
            or row["config_hash"] not in live_trial_set
            or row["registered_at"] < latest_at
            or row["registered_at"] < (attempt["started_at"] if attempt else 0)
            or (terminal_at is not None and row["registered_at"] > terminal_at)
            or row["prev_candidate_hash"] != candidate_head
            or row["candidate_hash"] != expected_hash
        ):
            ledger_failures.append(f"search_candidate:{row['candidate_seq']}")
        elif attempt is not None:
            attempt["candidates"].append(row["config_hash"])
        candidate_identities.add(identity)
        latest_candidate_times[row["search_id"]] = row["registered_at"]
        candidate_head = row["candidate_hash"]

    link_head = ""
    linked_identities: set[tuple[str, str]] = set()
    latest_link_times: dict[str, int] = {}
    for expected_sequence, row in enumerate(search_evaluation_rows, start=1):
        attempt = live_sessions.get(row["search_id"])
        evaluation = evaluations_by_run.get(row["run_id"])
        identity = (row["search_id"], row["run_id"])
        latest_at = latest_link_times.get(row["search_id"], -1)
        terminal_at = None if attempt is None else attempt["completed_at"]
        expected_hash = _alpha_trial_search_hash(
            {
                "config_hash": row["config_hash"],
                "evaluation_content_hash": row["evaluation_content_hash"],
                "prev_link_hash": row["prev_link_hash"],
                "recorded_at": row["recorded_at"],
                "run_id": row["run_id"],
                "search_id": row["search_id"],
            }
        )
        if (
            row["link_seq"] != expected_sequence
            or attempt is None
            or not attempt["candidates"]
            or not isinstance(row["config_hash"], str)
            or config_hash_pattern.fullmatch(row["config_hash"]) is None
            or row["config_hash"] not in attempt["candidates"]
            or identity in linked_identities
            or evaluation is None
            or evaluation["dataset_version"] != attempt["dataset_version"]
            or evaluation["content_hash"] != row["evaluation_content_hash"]
            or row["recorded_at"] < latest_at
            or row["recorded_at"] < (attempt["started_at"] if attempt else 0)
            or (terminal_at is not None and row["recorded_at"] > terminal_at)
            or row["prev_link_hash"] != link_head
            or row["link_hash"] != expected_hash
        ):
            ledger_failures.append(f"search_evaluation:{row['link_seq']}")
        elif attempt is not None:
            attempt["evaluation_links"].append(
                {
                    "config_hash": row["config_hash"],
                    "run_id": row["run_id"],
                    "recorded_at": row["recorded_at"],
                    "content_hash": row["evaluation_content_hash"],
                    "link_hash": row["link_hash"],
                }
            )
        linked_identities.add(identity)
        latest_link_times[row["search_id"]] = row["recorded_at"]
        link_head = row["link_hash"]

    for search_id, provenance in history_sessions.items():
        attempt = live_sessions.get(search_id)
        if attempt is None or attempt["status"] != "COMPLETED":
            ledger_failures.append(f"history_session:{search_id}")
            continue
        live_provenance = {
            "search_kind": attempt["search_kind"],
            "source": attempt["source"],
            "dataset_version": attempt["dataset_version"],
            "started_at": attempt["started_at"],
            "completed_at": attempt["completed_at"],
            "terminal_event_hash": attempt["terminal_event_hash"],
            "candidates": sorted(attempt["candidates"]),
            "evaluation_links": sorted(
                attempt["evaluation_links"], key=lambda item: item["run_id"]
            ),
        }
        if provenance != live_provenance:
            ledger_failures.append(f"history_session_binding:{search_id}")
    if ledger_failures:
        return _result(
            "FAILED",
            "M2",
            evidence,
            "append-only history does not match the portable snapshot: "
            + ", ".join(sorted(set(ledger_failures))),
        )

    maturity = args.get("pass_maturity", "M4")
    return _result(
        "PASS",
        maturity,
        evidence,
        "official guided/evolutionary/GP/MCTS and factory recorder wiring, a portable sealed "
        "snapshot, a versioned two-session monotonic history chain with exact completed-search "
        "evaluation bindings, and database-enforced append-only trial/evaluation/search guards "
        "are verified",
    )


def _l2_source_contract_error(contract: Any) -> str | None:
    if not isinstance(contract, dict) or set(contract) != {"version", "sources"}:
        return "source contract must contain only version and sources"
    if contract.get("version") != 2:
        return "source contract version must be 2"
    sources = contract.get("sources")
    if not isinstance(sources, list) or not sources:
        return "source contract must declare at least one source"

    paths: list[str] = []
    roles: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != {
            "relative_path",
            "required",
            "role",
        }:
            return "source entries must contain only relative_path, required, and role"
        relative_path = source.get("relative_path")
        role = source.get("role")
        required = source.get("required")
        if (
            not isinstance(relative_path, str)
            or not relative_path.startswith("data/cache/")
            or relative_path != relative_path.replace("\\", "/")
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
        ):
            return "source paths must be normalized repository-local data/cache paths"
        if (
            not isinstance(role, str)
            or not role
            or role != role.strip()
            or not role.replace("_", "").isalnum()
        ):
            return "source roles must be non-empty normalized identifiers"
        if type(required) is not bool:
            return "source requiredness must be boolean"
        paths.append(relative_path)
        roles.append(role)

    if len(paths) != len(set(paths)) or len(roles) != len(set(roles)):
        return "source paths and roles must be unique"
    if paths != sorted(paths):
        return "source contract entries must be sorted by relative_path"
    return None


def _l2_source_contract_sha256(contract: dict[str, Any]) -> str:
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _l2_backup_evidence(
    repo_root: Path,
    args: dict[str, Any],
    _target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    implementation_paths = args.get("implementation_paths", [])
    expected_contract = args.get("expected_source_contract")
    max_rpo_ms = args.get("max_rpo_ms")
    max_rto_ms = args.get("max_rto_ms")
    contract_error = _l2_source_contract_error(expected_contract)
    if (
        not isinstance(path, str)
        or not isinstance(implementation_paths, list)
        or contract_error is not None
        or type(max_rpo_ms) is not int
        or max_rpo_ms <= 0
        or type(max_rto_ms) is not int
        or max_rto_ms <= 0
    ):
        detail = "checker arguments are incomplete"
        if contract_error is not None:
            detail = f"invalid expected source contract: {contract_error}"
        return _result("UNVERIFIED", "M0", [], detail)
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return _result("UNVERIFIED", "M0", [], "evidence path must stay within the repository")
    if any(
        not isinstance(item, str) or Path(item).is_absolute() or ".." in Path(item).parts
        for item in implementation_paths
    ):
        return _result("UNVERIFIED", "M0", [], "implementation paths must stay within the repository")
    missing_implementation = [item for item in implementation_paths if not (repo_root / item).is_file()]
    evidence = [*implementation_paths]
    if missing_implementation:
        return _result(
            "FAILED",
            "M0",
            evidence,
            "missing backup implementation: " + ", ".join(missing_implementation),
        )
    evidence_path = repo_root / relative
    if not evidence_path.is_file():
        return _result(
            "PARTIAL",
            "M2",
            evidence,
            "encrypted backup implementation exists but no operational external-destination evidence exists",
        )
    evidence.append(path)
    try:
        document = load_json_without_duplicate_keys(
            evidence_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return _result("FAILED", "M1", evidence, f"invalid L2 backup evidence: {exc}")
    if not isinstance(document, dict):
        return _result("FAILED", "M1", evidence, "L2 backup evidence must be an object")

    required = {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": "L2_AUTHORITY_STATE",
        "encrypted": True,
        "manifest_verified": True,
        "source_contract_verified": True,
        "restore_verified": True,
        "sqlite_integrity_verified": True,
        "restore_history_verified": True,
        "rto_met": True,
        "rpo_met": True,
    }
    failures = [key for key, expected in required.items() if document.get(key) != expected]
    expected_digest = _l2_source_contract_sha256(expected_contract)
    recorded_contract = document.get("source_contract")
    recorded_digest = document.get("source_contract_sha256")
    if recorded_contract != expected_contract:
        failures.append("source_contract")
    if recorded_digest != expected_digest:
        failures.append("source_contract_sha256")
    if isinstance(recorded_contract, dict):
        try:
            if _l2_source_contract_sha256(recorded_contract) != recorded_digest:
                failures.append("source_contract_digest")
        except (TypeError, ValueError):
            failures.append("source_contract_digest")
    else:
        failures.append("source_contract_digest")

    expected_sources = {
        source["relative_path"]: source for source in expected_contract["sources"]
    }
    required_paths = {
        path for path, source in expected_sources.items() if source["required"]
    }
    optional_paths = set(expected_sources) - required_paths
    backed_up_sources = document.get("backed_up_sources")
    missing_optional_sources = document.get("missing_optional_sources")
    backed_paths: list[str] = []
    if not isinstance(backed_up_sources, list) or not backed_up_sources:
        failures.append("backed_up_sources")
    else:
        for source in backed_up_sources:
            if not isinstance(source, dict):
                failures.append("backed_up_sources")
                continue
            relative_path = source.get("relative_path")
            if not isinstance(relative_path, str):
                failures.append("backed_up_sources")
                continue
            backed_paths.append(relative_path)
            if source != expected_sources.get(relative_path):
                failures.append("backed_up_sources")
        if backed_paths != sorted(backed_paths) or len(backed_paths) != len(
            set(backed_paths)
        ):
            failures.append("backed_up_sources")

    missing_paths: list[str] = []
    if not isinstance(missing_optional_sources, list) or any(
        not isinstance(item, str) for item in missing_optional_sources
    ):
        failures.append("missing_optional_sources")
    else:
        missing_paths = missing_optional_sources
        if missing_paths != sorted(missing_paths) or len(missing_paths) != len(
            set(missing_paths)
        ):
            failures.append("missing_optional_sources")

    backed_path_set = set(backed_paths)
    missing_path_set = set(missing_paths)
    if (
        not required_paths <= backed_path_set
        or not missing_path_set <= optional_paths
        or backed_path_set & missing_path_set
        or backed_path_set | missing_path_set != set(expected_sources)
    ):
        failures.append("source_partition")

    numeric = (
        "observed_at_ms",
        "file_count",
        "required_source_count",
        "restore_elapsed_ms",
        "rto_ms",
        "source_age_ms",
        "max_rpo_ms",
        "backup_elapsed_ms",
    )
    if any(type(document.get(key)) is not int for key in numeric):
        failures.append("numeric_fields")
    elif (
        document["observed_at_ms"] < 0
        or document["file_count"] != len(backed_paths)
        or document["required_source_count"] != len(required_paths)
        or document["restore_elapsed_ms"] < 0
        or document["rto_ms"] <= 0
        or document["restore_elapsed_ms"] > document["rto_ms"]
        or document["rto_ms"] > max_rto_ms
        or document["source_age_ms"] < 0
        or document["max_rpo_ms"] <= 0
        or document["source_age_ms"] > document["max_rpo_ms"]
        or document["max_rpo_ms"] > max_rpo_ms
        or document["backup_elapsed_ms"] < 0
    ):
        failures.append("thresholds")
    if failures:
        return _result(
            "FAILED",
            "M1",
            evidence,
            "L2 backup evidence failed: " + ", ".join(sorted(set(failures))),
        )

    external = document.get("external_destination_verified") is True
    destination_class = document.get("destination_class")
    if not external or destination_class not in {"OFF_HOST_UNC", "SEPARATE_VOLUME"}:
        return _result(
            "PARTIAL",
            "M2",
            evidence,
            "backup is encrypted and restore-verified but the recorded destination is not external",
        )
    return _result(
        "PASS",
        "M3",
        evidence,
        "external encrypted authority-state backup, exact source contract, manifest, SQLite integrity, and isolated restore verified",
    )


def _l2_restore_history_evidence(
    repo_root: Path,
    args: dict[str, Any],
    _target: str,
    context: AssessmentContext,
) -> dict[str, Any]:
    history_path = args.get("history_path")
    checkpoint_path = args.get("checkpoint_path")
    trust_registry_path = args.get("trust_registry_path")
    implementation_paths = args.get("implementation_paths", [])
    expected_contract = args.get("expected_source_contract")
    required_issuer = args.get("required_checkpoint_issuer")
    required_key_id = args.get("required_checkpoint_key_id")
    contract_error = _l2_source_contract_error(expected_contract)
    relative_paths = (history_path, checkpoint_path, trust_registry_path)
    if (
        any(not isinstance(item, str) for item in relative_paths)
        or not isinstance(implementation_paths, list)
        or any(not isinstance(item, str) for item in implementation_paths)
        or contract_error is not None
        or not isinstance(required_issuer, str)
        or not required_issuer.strip()
        or not isinstance(required_key_id, str)
        or not required_key_id.strip()
    ):
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            "restore-history checker arguments are incomplete",
        )
    all_declared_paths = [*relative_paths, *implementation_paths]
    if any(
        Path(item).is_absolute() or ".." in Path(item).parts
        for item in all_declared_paths
    ):
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            "restore-history paths must stay within the repository",
        )

    expected_digest = _l2_source_contract_sha256(expected_contract)
    policy_fields = {
        "max_attempt_age_ms": args.get("max_attempt_age_ms"),
        "max_success_age_ms": args.get("max_success_age_ms"),
        "max_attempt_gap_ms": args.get("max_attempt_gap_ms"),
        "max_success_gap_ms": args.get("max_success_gap_ms"),
        "minimum_history_span_ms": args.get("minimum_history_span_ms"),
        "minimum_successes": args.get("minimum_successes"),
        "clean_lookback_ms": args.get("clean_lookback_ms"),
        "attempt_timeout_ms": args.get("attempt_timeout_ms"),
    }
    checkpoint_policy = {
        "max_future_skew_ms": args.get("max_checkpoint_future_skew_ms"),
        "max_seal_age_ms": args.get("max_checkpoint_age_ms"),
        "minimum_retention_window_ms": args.get(
            "minimum_checkpoint_retention_ms"
        ),
    }
    try:
        policy = RestoreCadencePolicy(
            source_contract_sha256=expected_digest,
            **policy_fields,
        )
        if any(type(value) is not int for value in checkpoint_policy.values()):
            raise ValueError("checkpoint policy values must be integers")
        if (
            checkpoint_policy["max_future_skew_ms"] < 0
            or checkpoint_policy["max_seal_age_ms"] <= 0
            or checkpoint_policy["minimum_retention_window_ms"] <= 0
        ):
            raise ValueError("checkpoint policy values are out of range")
    except (TypeError, ValueError) as exc:
        return _result(
            "UNVERIFIED",
            "M0",
            [],
            f"invalid restore-history policy: {exc}",
        )

    evidence = list(implementation_paths)
    missing_implementation = [
        item for item in implementation_paths if not (repo_root / item).is_file()
    ]
    if missing_implementation:
        return _result(
            "FAILED",
            "M0",
            evidence,
            "missing restore-history implementation: "
            + ", ".join(missing_implementation),
        )

    history_relative = Path(history_path)
    evidence.append(history_path)
    if not (repo_root / history_relative).is_file():
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "restore-history authority is not provisioned; recurring M4 evidence is unavailable",
        )

    store: L2RestoreHistoryStore | None = None
    try:
        store = L2RestoreHistoryStore(
            str(repo_root / history_relative),
            create=False,
            clock_ms=lambda: context.as_of_ms,
        )
        snapshot = store.read_snapshot()
    except (OSError, L2RestoreHistoryStoreError, TypeError, ValueError) as exc:
        return _result(
            "FAILED",
            "M1",
            evidence,
            f"restore-history authority is invalid: {exc}",
        )
    finally:
        if store is not None:
            store.close()

    health = evaluate_cadence(snapshot, policy, now_ms=context.as_of_ms)
    if health.verdict == "UNAVAILABLE":
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "restore-history authority has no operational attempts",
        )
    if health.verdict != "PASS":
        return _result(
            "FAILED",
            "M3",
            evidence,
            "restore-history health failed: " + ", ".join(health.reason_codes),
        )
    if health.authority_head is None:
        return _result(
            "FAILED",
            "M1",
            evidence,
            "restore-history authority has no verified head",
        )

    checkpoint_file = repo_root / Path(checkpoint_path)
    trust_file = repo_root / Path(trust_registry_path)
    if not checkpoint_file.is_file() or not trust_file.is_file():
        return _result(
            "PARTIAL",
            "M3",
            evidence,
            "local recurring restore history is healthy but an external signed checkpoint is missing",
        )
    evidence.extend([checkpoint_path, trust_registry_path])
    try:
        trust_document = load_json_without_duplicate_keys(
            trust_file.read_text(encoding="utf-8")
        )
        if (
            not isinstance(trust_document, dict)
            or set(trust_document) != {"schema_version", "keys"}
            or trust_document.get("schema_version") != 1
            or not isinstance(trust_document.get("keys"), list)
        ):
            raise ValueError("restore checkpoint trust registry is invalid")
        trusted_keys: dict[
            tuple[str, str], TrustedL2RestoreCheckpointKey
        ] = {}
        for raw_key in trust_document["keys"]:
            key = TrustedL2RestoreCheckpointKey.model_validate(raw_key)
            identity = (key.issuer, key.key_id)
            if identity in trusted_keys:
                raise ValueError("duplicate restore checkpoint trusted key")
            trusted_keys[identity] = key
        if not trusted_keys:
            return _result(
                "PARTIAL",
                "M3",
                evidence,
                "local recurring restore history is healthy but the external checkpoint trust registry is empty",
            )
        envelope = load_signed_l2_restore_checkpoint_json(
            checkpoint_file.read_bytes()
        )
        verify_signed_l2_restore_checkpoint(
            envelope,
            trusted_keys=trusted_keys,
            expected_local_head=L2RestoreHistoryHead(
                store_kind=L2_RESTORE_HISTORY_STORE_KIND,
                store_schema_version=L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
                event_count=health.authority_head.event_seq,
                head_seq=health.authority_head.event_seq,
                head_hash=health.authority_head.event_hash,
            ),
            required_issuer=required_issuer,
            required_key_id=required_key_id,
            now_ms=context.as_of_ms,
            **checkpoint_policy,
        )
    except (
        OSError,
        L2RestoreCheckpointError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        return _result(
            "FAILED",
            "M2",
            evidence,
            f"external restore-history checkpoint failed: {exc}",
        )

    return _result(
        "PASS",
        "M4",
        evidence,
        "recurring external L2 restore history, dynamic freshness, retained failures, exact authority head, signed checkpoint, and retention verified",
    )


def _risk_recovery_evidence(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    if not isinstance(path, str):
        return _result("UNVERIFIED", "M0", [], "checker requires an evidence path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return _result("UNVERIFIED", "M0", [], "evidence path must stay within the repository")
    evidence_path = repo_root / relative
    if not evidence_path.is_file():
        return _result("FAILED", "M2", [path], f"missing: {path}")
    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("FAILED", "M2", [path], f"invalid risk recovery evidence: {exc}")
    if not isinstance(document, dict):
        return _result("FAILED", "M2", [path], "risk recovery evidence must be an object")

    required = {
        "schema_version": 2,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "rto_met": True,
        "artifact_names": [
            "audit.db",
            "kill_switch.db",
            "kill_switch.json",
            "ledger.db",
        ],
        "recovered_state_count": 4,
        "kill_switch_persisted": True,
        "kill_switch_chain_verified": True,
        "kill_switch_event_recoverable": True,
        "kill_switch_outbox_pending_count": 1,
        "gate_rejected_while_tripped": True,
        "gate_rejection_code": "KILL_SWITCH_ACTIVE",
        "ledger_recovered": True,
        "audit_chain_verified": True,
        "corrupt_kill_switch_failed_closed": True,
        "tampered_kill_switch_detected": True,
        "tampered_audit_detected": True,
        "active_state_untouched": True,
    }
    failures = [key for key, expected in required.items() if document.get(key) != expected]
    evidence = [path]
    continuous_markers = args.get("continuous_markers")
    if continuous_markers is not None:
        if not isinstance(continuous_markers, dict) or not continuous_markers:
            failures.append("continuous_markers")
        else:
            for continuous_path in sorted(continuous_markers, key=str):
                needles = continuous_markers[continuous_path]
                candidate = Path(continuous_path) if isinstance(continuous_path, str) else None
                if candidate is None or candidate.is_absolute() or ".." in candidate.parts:
                    failures.append("continuous_path")
                    continue
                evidence.append(continuous_path)
                if (
                    not isinstance(needles, list)
                    or not needles
                    or any(not isinstance(needle, str) or not needle for needle in needles)
                ):
                    failures.append(f"continuous_markers:{continuous_path}")
                    continue
                continuous_file = repo_root / candidate
                if not continuous_file.is_file():
                    failures.append(f"continuous_missing:{continuous_path}")
                    continue
                try:
                    continuous_text = continuous_file.read_text(encoding="utf-8")
                except OSError:
                    failures.append(f"continuous_unreadable:{continuous_path}")
                    continue
                if any(needle not in continuous_text for needle in needles):
                    failures.append(f"continuous_marker:{continuous_path}")
    numeric = ("observed_at_ms", "elapsed_ms", "rto_ms")
    if any(type(document.get(key)) is not int for key in numeric):
        failures.append("numeric_fields")
    elif document["observed_at_ms"] < 0 or document["elapsed_ms"] < 0 or document["rto_ms"] <= 0:
        failures.append("numeric_ranges")
    elif document["elapsed_ms"] > document["rto_ms"]:
        failures.append("rto_threshold")

    pre = document.get("pre_restart_sha256")
    post = document.get("post_restart_sha256")
    expected_names = {
        "audit.db",
        "kill_switch.db",
        "kill_switch.json",
        "ledger.db",
    }
    if not isinstance(pre, dict) or set(pre) != expected_names or pre != post:
        failures.append("state_digests")
    elif any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in pre.values()
    ):
        failures.append("digest_shape")

    ledger_before = document.get("ledger_before")
    ledger_after = document.get("ledger_after")
    if not isinstance(ledger_before, dict) or ledger_before != ledger_after:
        failures.append("ledger_summary")
    else:
        orders = ledger_before.get("orders")
        fills = ledger_before.get("fills")
        positions = ledger_before.get("positions")
        if (
            not isinstance(orders, list)
            or not orders
            or not isinstance(fills, list)
            or not fills
            or not isinstance(positions, dict)
            or not positions
        ):
            failures.append("ledger_summary_empty")

    if failures:
        return _result(
            "FAILED",
            "M3",
            evidence,
            "risk recovery evidence failed: " + ", ".join(sorted(set(failures))),
        )
    maturity = args.get("pass_maturity", target)
    return _result(
        "PASS",
        maturity,
        evidence,
        "isolated risk-state restart, gate rejection, ledger recovery, "
        "audit integrity, fault injection, and scheduled fresh-drill markers verified",
    )


def _supply_chain_baseline_evidence(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    if not isinstance(path, str):
        return _result("UNVERIFIED", "M0", [], "checker requires an evidence path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return _result("UNVERIFIED", "M0", [], "evidence path must stay within the repository")
    evidence_path = repo_root / relative
    if not evidence_path.is_file():
        return _result("FAILED", "M1", [path], f"missing: {path}")
    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("FAILED", "M1", [path], f"invalid supply-chain evidence: {exc}")
    if not isinstance(document, dict):
        return _result("FAILED", "M1", [path], "supply-chain evidence must be an object")

    external_sigstore_status = document.get(
        "artifact_admission_external_bundle_status"
    )
    external_sigstore_verified = (
        external_sigstore_status
        == "VERIFIED_EXTERNAL_GITHUB_OIDC_FULCIO_SCT_REKOR_V1"
    )
    required = {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_REPOSITORY_BASELINE",
        "sbom_format": "CycloneDX",
        "sbom_spec_version": "1.6",
        "python_requirements_all_pinned": True,
        "npm_lockfile_version": 3,
        "actions_all_sha_pinned": True,
        "sast_gate_wired": True,
        "reproducible_inputs": True,
        "vulnerability_scan_status": ("PARTIAL_EXTERNAL_NPM_PYTHON_ACTIONS_CONTAINER_VERIFIED"),
        "npm_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "npm_vulnerability_count": 0,
        "python_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "python_vulnerability_count": 0,
        "actions_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "actions_vulnerability_count": 0,
        "container_vulnerability_evidence_status": ("VERIFIED_EXTERNAL_BASE_REMEDIATED_RUNTIME_ZERO_KNOWN"),
        "container_runtime_vulnerability_count": 0,
        "container_reproducibility_evidence_status": ("VERIFIED_LOCAL_TWO_COLD_BUILDS_BYTE_IDENTICAL"),
        "artifact_signature_status": (
            "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST"
            if external_sigstore_verified
            else "VERIFIER_READY_EXTERNAL_SIGNATURE_MISSING"
        ),
        "artifact_admission_verifier_status": (
            "OFFLINE_SIGSTORE_REKOR_V1_REAL_EXTERNAL_BUNDLE_VERIFIED_POLICY_V3_DECISION_V4_EXTERNAL_RECEIPT_STILL_GATED"
            if external_sigstore_verified
            else "OFFLINE_SIGSTORE_REKOR_V1_POLICY_V3_DECISION_V4_SIGNED_RECEIPT_GATED_CODE_VERIFIED_NO_VALID_BUNDLE"
        ),
        "artifact_admission_contract_versions": {
            "policy": 3,
            "decision": 4,
            "verification_receipt": 1,
            "signed_receipt_envelope": 1,
            "policy_registry": 2,
            "receipt_authority_registry": 2,
            "decision_store": 4,
        },
        "artifact_admission_policy_registry_ref": ("config/artifact_admission_policy_registry.json"),
        "artifact_admission_policy_registry_status": ("L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_POLICIES"),
        "artifact_admission_active_policy_count": 0,
        "artifact_admission_receipt_authority_registry_ref": ("config/artifact_receipt_authority_registry.json"),
        "artifact_admission_receipt_authority_registry_status": ("L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_KEYS"),
        "artifact_admission_active_receipt_key_count": 0,
        "artifact_admission_library": "sigstore",
        "artifact_admission_library_version": "4.4.0",
        "artifact_admission_trusted_root_mode": "CALLER_SUPPLIED_SHA256_PINNED",
        "artifact_admission_identity_policy": "EXACT_OIDC_ISSUER_AND_SUBJECT",
        "artifact_admission_transparency_verification": (
            "SIGSTORE_SCT_REKOR_V1_INCLUSION_SIGNED_RECEIPT_ATTESTS_CHECKPOINT_TIME"
        ),
        "artifact_admission_freshness_revocation_status": (
            "LOCAL_FAIL_CLOSED_ED25519_SIGNED_RECEIPT_ENFORCED_SOURCE_NATIVE_PROOFS_NOT_VERIFIED"
        ),
        "artifact_admission_receipt_authenticity_status": (
            "VERIFY_ONLY_ED25519_AUTHORITY_REGISTRY_EMPTY_NO_EXTERNAL_RECEIPT"
        ),
        "artifact_admission_decision_store_path": ("data/cache/artifact_admission.db"),
        "artifact_admission_decision_store_worm_status": ("NOT_WORM_LOCAL_FILE_REPLACEABLE"),
        "artifact_admission_registry_history_status": (
            "LOCAL_SINGLE_SQLITE_LINEARIZABLE_REGISTRY_DECISION_CAS_UNSEALED"
        ),
        "artifact_admission_registry_mirror_mode": (
            "REPOSITORY_JSON_EXPLICIT_PROVISIONING_MIRROR_NOT_RUNTIME_AUTHORITY"
        ),
        "artifact_admission_allow_cas_status": ("EXACT_POLICY_AND_RECEIPT_AUTHORITY_HEADS_TRANSACTIONALLY_ENFORCED"),
        "artifact_admission_authority_clock_status": (
            "LOCAL_MONOTONIC_GUARDED_WALL_CLOCK_FUTURE_SKEW_NO_EXTERNAL_TIME_AUTHORITY"
        ),
        "artifact_admission_read_consistency_status": ("SINGLE_SQLITE_TRANSACTIONAL_READ_SNAPSHOT"),
        "artifact_admission_valid_bundle_fixture_status": (
            "VERIFIED_RETAINED_EXTERNAL_SIGSTORE_DSSE_BUNDLE"
            if external_sigstore_verified
            else "NOT_PROVIDED"
        ),
        "artifact_admission_signing_capability_present": False,
        "artifact_admission_external_bundle_status": (
            "VERIFIED_EXTERNAL_GITHUB_OIDC_FULCIO_SCT_REKOR_V1"
            if external_sigstore_verified
            else "NOT_PROVIDED"
        ),
        "artifact_admission_independent_deployment_status": "NOT_PROVIDED",
    }
    failures = [key for key, expected in required.items() if document.get(key) != expected]
    external_sigstore_evidence: list[str] = []
    if external_sigstore_verified:
        external_ref = document.get(
            "artifact_admission_external_bundle_evidence_ref"
        )
        external_relative = (
            Path(external_ref) if isinstance(external_ref, str) else None
        )
        if (
            external_relative is None
            or external_relative.is_absolute()
            or ".." in external_relative.parts
        ):
            failures.append("artifact_admission_external_bundle_evidence_ref")
        else:
            try:
                external_report = verify_external_sigstore_evidence(
                    repo_root / external_relative
                )
            except ExternalSigstoreEvidenceError:
                failures.append("artifact_admission_external_bundle_evidence")
            else:
                external_sigstore_evidence.extend(
                    str(path.relative_to(repo_root)).replace("\\", "/")
                    for path in sorted(
                        (repo_root / external_relative).glob("*"),
                        key=lambda item: item.name,
                    )
                )
                if (
                    document.get(
                        "artifact_admission_external_bundle_evidence"
                    )
                    != external_report
                ):
                    failures.append(
                        "artifact_admission_external_bundle_evidence"
                    )
    elif (
        document.get("artifact_admission_external_bundle_evidence_ref")
        is not None
        or document.get("artifact_admission_external_bundle_evidence")
        is not None
    ):
        failures.append("stale_artifact_admission_external_bundle_evidence")
    external_seal_status = document.get(
        "artifact_admission_decision_history_external_seal_status"
    )
    if external_seal_status == "NOT_PROVIDED":
        pass
    elif external_seal_status == (
        "SEPARATE_VOLUME_CREATE_ONCE_VERIFIED_WORM_UNVERIFIED"
    ):
        digest_pattern = re.compile(r"^[0-9a-f]{64}$")
        if (
            document.get(
                "artifact_admission_decision_history_external_seal_evidence_ref"
            )
            != "evidence/artifact_admission_seal_latest.json"
            or not isinstance(
                document.get(
                    "artifact_admission_decision_history_external_seal_evidence_sha256"
                ),
                str,
            )
            or digest_pattern.fullmatch(
                document[
                    "artifact_admission_decision_history_external_seal_evidence_sha256"
                ]
            )
            is None
            or not isinstance(
                document.get(
                    "artifact_admission_decision_history_external_anchor_sha256"
                ),
                str,
            )
            or digest_pattern.fullmatch(
                document[
                    "artifact_admission_decision_history_external_anchor_sha256"
                ]
            )
            is None
            or document.get(
                "artifact_admission_decision_history_external_destination_class"
            )
            not in {"SEPARATE_VOLUME", "OFF_HOST_UNC"}
            or document.get(
                "artifact_admission_decision_history_external_signature_verified"
            )
            is not False
            or document.get(
                "artifact_admission_decision_history_external_worm_verified"
            )
            is not False
        ):
            failures.append(
                "artifact_admission_decision_history_external_seal"
            )
    else:
        failures.append("artifact_admission_decision_history_external_seal_status")
    numeric = (
        "component_count",
        "python_component_count",
        "npm_locked_components",
        "npm_integrity_components",
    )
    if any(type(document.get(key)) is not int for key in numeric):
        failures.append("component_counts")
    elif (
        document["component_count"] <= 0
        or document["python_component_count"] <= 0
        or document["npm_locked_components"] <= 0
        or document["npm_integrity_components"] <= 0
        or document["npm_integrity_components"] > document["npm_locked_components"]
    ):
        failures.append("component_count_ranges")

    blockers = document.get("m4_blockers")
    required_blockers = {
        "CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN",
        "INDEPENDENT_ADMISSION_DEPLOYMENT_NOT_PROVEN",
        "EXTERNAL_RECEIPT_AUTHORITY_AND_SIGNED_RECEIPT_NOT_PROVIDED",
        "SOURCE_NATIVE_CHECKPOINT_REVOCATION_PROOFS_NOT_VERIFIED",
        "ARTIFACT_ADMISSION_DECISION_HISTORY_NOT_EXTERNALLY_SEALED",
    }
    if not external_sigstore_verified:
        required_blockers.update(
            {
                "EXTERNAL_ARTIFACT_SIGNATURE_IDENTITY_MISSING",
                "SIGNED_PROVENANCE_BUNDLE_NOT_RETAINED",
            }
        )
    provisioning_status = document.get(
        "artifact_admission_decision_store_provisioning_status"
    )
    admission_store_provisioned = (
        provisioning_status
        == "LOCAL_PROVISIONED_VERIFIED_EMPTY_TRUST_ROOTS_UNSEALED"
    )
    if provisioning_status == "NOT_PROVIDED":
        required_blockers.add(
            "ARTIFACT_ADMISSION_FIXED_AUTHORITY_STORE_NOT_PROVISIONED"
        )
    elif not admission_store_provisioned:
        failures.append("artifact_admission_decision_store_provisioning_status")
    if (
        not isinstance(blockers, list)
        or not required_blockers <= set(blockers)
        or "CONTAINER_BUILD_REPRODUCIBILITY_NOT_PROVEN" in blockers
        or "BASE_IMAGE_VULNERABILITY_EVIDENCE_MISSING" in blockers
        or "CONTAINER_VULNERABILITY_POLICY_FAILED" in blockers
    ):
        failures.append("m4_blockers")
    elif admission_store_provisioned and (
        "ARTIFACT_ADMISSION_FIXED_AUTHORITY_STORE_NOT_PROVISIONED" in blockers
    ):
        failures.append("stale_artifact_admission_store_blocker")

    admission_evidence: list[str] = []
    registry_bytes = b""
    receipt_registry_bytes = b""
    registry_ref = document.get("artifact_admission_policy_registry_ref")
    registry_relative = Path(registry_ref) if isinstance(registry_ref, str) else None
    if registry_relative is None or registry_relative.is_absolute() or ".." in registry_relative.parts:
        failures.append("artifact_admission_policy_registry_ref")
    else:
        try:
            registry_bytes = (repo_root / registry_relative).read_bytes()
            registry = load_json_without_duplicate_keys(registry_bytes)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            failures.append("artifact_admission_policy_registry")
        else:
            admission_evidence_ref = str(registry_relative).replace("\\", "/")
            if (
                source_file_sha256(repo_root / registry_relative)
                != document.get("artifact_admission_policy_registry_sha256")
                or not isinstance(registry, dict)
                or set(registry) != {"schema_version", "readiness", "generation", "policies"}
                or registry.get("schema_version") != 2
                or registry.get("readiness") != "L2_DRY_RUN_ONLY"
                or type(registry.get("generation")) is not int
                or registry["generation"] < 0
                or document.get("artifact_admission_policy_registry_generation")
                != registry["generation"]
                or registry.get("policies") != {}
            ):
                failures.append("artifact_admission_policy_registry")
            else:
                admission_evidence.append(admission_evidence_ref)

    receipt_registry_ref = document.get("artifact_admission_receipt_authority_registry_ref")
    receipt_registry_relative = Path(receipt_registry_ref) if isinstance(receipt_registry_ref, str) else None
    if (
        receipt_registry_relative is None
        or receipt_registry_relative.is_absolute()
        or ".." in receipt_registry_relative.parts
    ):
        failures.append("artifact_admission_receipt_authority_registry_ref")
    else:
        try:
            receipt_registry_bytes = (repo_root / receipt_registry_relative).read_bytes()
            receipt_registry = load_json_without_duplicate_keys(receipt_registry_bytes)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            failures.append("artifact_admission_receipt_authority_registry")
        else:
            normalized_ref = str(receipt_registry_relative).replace("\\", "/")
            if (
                source_file_sha256(repo_root / receipt_registry_relative)
                != document.get("artifact_admission_receipt_authority_registry_sha256")
                or not isinstance(receipt_registry, dict)
                or set(receipt_registry) != {"schema_version", "readiness", "generation", "keys"}
                or receipt_registry.get("schema_version") != 2
                or receipt_registry.get("readiness") != "L2_DRY_RUN_ONLY"
                or type(receipt_registry.get("generation")) is not int
                or receipt_registry["generation"] < 0
                or document.get(
                    "artifact_admission_receipt_authority_registry_generation"
                )
                != receipt_registry["generation"]
                or receipt_registry.get("keys") != {}
            ):
                failures.append("artifact_admission_receipt_authority_registry")
            else:
                admission_evidence.append(normalized_ref)

    decision_store_path = document.get("artifact_admission_decision_store_path")
    decision_store_relative = Path(decision_store_path) if isinstance(decision_store_path, str) else None
    if (
        decision_store_relative is None
        or decision_store_relative.is_absolute()
        or ".." in decision_store_relative.parts
    ):
        failures.append("artifact_admission_decision_store_provisioning")
    elif admission_store_provisioned:
        store_path = repo_root / decision_store_relative
        snapshot = document.get("artifact_admission_authority_snapshot")
        if (
            document.get("artifact_admission_decision_store_status")
            != "WIRED_LOCAL_PROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"
            or not isinstance(snapshot, dict)
            or store_path.is_symlink()
            or not store_path.is_file()
        ):
            failures.append("artifact_admission_decision_store_provisioning")
        else:
            store: ArtifactAdmissionStore | None = None
            try:
                store = ArtifactAdmissionStore(str(store_path), create=False)
                heads = store.registry_heads()
                expected_registry_bytes = {
                    POLICY_REGISTRY_KIND: registry_bytes,
                    RECEIPT_AUTHORITY_REGISTRY_KIND: receipt_registry_bytes,
                }
                if set(heads) != set(expected_registry_bytes):
                    raise ValueError("registry heads are incomplete")
                for kind, content in expected_registry_bytes.items():
                    head = heads[kind]
                    if (
                        head.registry_bytes != content
                        or head.registry_sha256
                        != hashlib.sha256(content).hexdigest()
                    ):
                        raise ValueError("registry head mirror mismatch")
                expected_snapshot = {
                    "authority_time_floor_ms": store.authority_time_floor(),
                    "decision_count": len(store.decisions()),
                    "policy_registry_event_hash": heads[
                        POLICY_REGISTRY_KIND
                    ].event_hash,
                    "policy_registry_event_seq": heads[
                        POLICY_REGISTRY_KIND
                    ].authority_event_seq,
                    "receipt_authority_registry_event_hash": heads[
                        RECEIPT_AUTHORITY_REGISTRY_KIND
                    ].event_hash,
                    "receipt_authority_registry_event_seq": heads[
                        RECEIPT_AUTHORITY_REGISTRY_KIND
                    ].authority_event_seq,
                }
                if snapshot != expected_snapshot:
                    raise ValueError("authority snapshot mismatch")
            except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError):
                failures.append("artifact_admission_decision_store_provisioning")
            finally:
                if store is not None:
                    store.close()
    elif (
        document.get("artifact_admission_decision_store_status")
        != "WIRED_FAIL_CLOSED_UNPROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"
        or document.get("artifact_admission_authority_snapshot") is not None
        or (repo_root / decision_store_relative).exists()
    ):
        failures.append("artifact_admission_decision_store_provisioning")

    admission_paths = {
        "services/governance/artifact_admission.py",
        "services/governance/artifact_admission_store.py",
        "services/governance/artifact_receipt_auth.py",
        "services/governance/external_sigstore_evidence.py",
        "tools/verify_artifact_admission.py",
        "tools/provision_artifact_admission_authority.py",
        "tools/artifact_admission_seal.py",
    }
    admission_hashes = document.get("artifact_admission_implementation_sha256")
    if not isinstance(admission_hashes, dict) or set(admission_hashes) != admission_paths:
        failures.append("artifact_admission_implementation_hashes")
    else:
        for implementation_path in sorted(admission_hashes):
            expected_digest = admission_hashes[implementation_path]
            admission_evidence.append(implementation_path)
            if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
                failures.append(f"artifact_admission_implementation_digest:{implementation_path}")
                continue
            try:
                actual_digest = source_file_sha256(repo_root / implementation_path)
            except OSError:
                failures.append(f"artifact_admission_implementation_missing:{implementation_path}")
            else:
                if actual_digest != expected_digest:
                    failures.append(f"artifact_admission_implementation_digest:{implementation_path}")

    required_admission_tests = {
        "tests/governance/test_artifact_admission.py",
        "tests/governance/test_artifact_admission_cli.py",
        "tests/governance/test_artifact_admission_store.py",
        "tests/governance/test_artifact_receipt_auth.py",
        "tests/governance/test_artifact_admission_provisioning.py",
        "tests/governance/test_external_sigstore_evidence.py",
        "tests/tools/test_artifact_admission_seal.py",
    }
    admission_tests = document.get("artifact_admission_verification_paths")
    if not isinstance(admission_tests, list) or set(admission_tests) != required_admission_tests:
        failures.append("artifact_admission_verification_paths")
    else:
        for verification_path in sorted(admission_tests):
            admission_evidence.append(verification_path)
            if not (repo_root / verification_path).is_file():
                failures.append(f"artifact_admission_verification_missing:{verification_path}")

    try:
        admission_core = (repo_root / "services/governance/artifact_admission.py").read_text(encoding="utf-8")
        admission_cli = (repo_root / "tools/verify_artifact_admission.py").read_text(encoding="utf-8")
        admission_store = (repo_root / "services/governance/artifact_admission_store.py").read_text(encoding="utf-8")
        receipt_auth = (repo_root / "services/governance/artifact_receipt_auth.py").read_text(encoding="utf-8")
        admission_provision = (
            repo_root / "tools/provision_artifact_admission_authority.py"
        ).read_text(encoding="utf-8")
        requirements_dev = (repo_root / "requirements-dev.txt").read_text(encoding="utf-8")
        project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        failures.append("artifact_admission_load_bearing_sources")
    else:
        required_cli_markers = (
            "TrustedRoot.from_file",
            "Verifier(trusted_root=",
            "Identity(",
            "verify_dsse",
            "POLICY_REGISTRY_PATH",
            "_policy_registry_decision",
            "--verification-receipt",
            "POLICY_NOT_TRUSTED",
            "POLICY_REVOKED",
            "DECISION_STORE_PATH",
            "ArtifactAdmissionStore",
            "create=False",
            "_persist_and_emit",
            "_runtime_verifier_id",
            "RECEIPT_AUTHORITY_REGISTRY_PATH",
            "_receipt_authority_registry_decision",
            "verify_signed_artifact_receipt",
            "POLICY_REGISTRY_CHANGED_DURING_ADMISSION",
            "RECEIPT_AUTHORITY_REGISTRY_CHANGED_DURING_ADMISSION",
            "expected_policy_registry_head",
            "expected_receipt_authority_registry_head",
            "_registry_mirror_reason",
        )
        required_core_markers = (
            "MAX_ARTIFACT_AGE_MS",
            "max_verification_age_ms",
            "max_checkpoint_age_ms",
            "max_revocation_age_ms",
            "CERTIFICATE_REVOKED",
            "REVOCATION_STATUS_UNKNOWN",
            "load_json_without_duplicate_keys",
            "VERIFIER_RUNTIME_MISMATCH",
            "required_receipt_authority_id",
            "required_receipt_public_key_sha256",
            "RECEIPT_AUTHENTICATION_REQUIRED",
        )
        required_store_markers = (
            "ArtifactAdmissionStore",
            "BEGIN IMMEDIATE",
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=FULL",
            "append-only",
            "ArtifactAdmissionStoreClockRollback",
            "ArtifactAdmissionStoreRegistryRollback",
            "ArtifactAdmissionStoreRegistryConflict",
            "artifact_admission_authority_events",
            "append_registry_snapshot",
            "ArtifactAdmissionStoreRegistryHeadChanged",
            "_validate_allow_registry_binding",
            "ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS",
            "_local_authority_clock_ms",
            "_read_verified_locked",
            "authority_time_floor",
        )
        required_receipt_auth_markers = (
            "Ed25519PublicKey",
            "SignedArtifactVerificationReceipt",
            "artifact_receipt_signing_payload",
            "verify_signed_artifact_receipt",
            "RECEIPT_SIGNATURE_INVALID",
        )
        required_provision_markers = (
            "create=not destination.exists()",
            "append_registry_snapshot",
            "_verify_exact",
            "check_only",
            "POLICY_REGISTRY_KIND",
            "RECEIPT_AUTHORITY_REGISTRY_KIND",
            '"signing_capability_present": False',
            '"live_trading_authority_granted": False',
        )
        forbidden_markers = (
            "sigstore.sign",
            "Signer(",
            "IdentityToken",
            "Verifier.production",
            "UnsafeNoOp",
            "PRIVATE KEY",
            "Ed25519PrivateKey",
        )
        if (
            "evaluate_verified_provenance" not in admission_core
            or any(marker not in admission_core for marker in required_core_markers)
            or any(marker not in admission_cli for marker in required_cli_markers)
            or any(marker not in admission_store for marker in required_store_markers)
            or any(marker not in receipt_auth for marker in required_receipt_auth_markers)
            or any(
                marker not in admission_provision
                for marker in required_provision_markers
            )
        ):
            failures.append("artifact_admission_load_bearing_markers")
        if any(
            marker
            in (
                admission_core
                + admission_store
                + receipt_auth
                + admission_cli
                + admission_provision
            )
            for marker in forbidden_markers
        ):
            failures.append("artifact_admission_forbidden_capability")
        supply_chain_dependencies = project.get("project", {}).get("optional-dependencies", {}).get("supply-chain", [])
        if "sigstore==4.4.0" not in requirements_dev.splitlines() or "sigstore==4.4.0" not in supply_chain_dependencies:
            failures.append("artifact_admission_dependency_pin")
    if (
        type(document.get("container_base_vulnerability_count")) is not int
        or document.get("container_base_vulnerability_count", -1) < 0
    ):
        failures.append("container_base_vulnerability_count")
    runtime_digest = document.get("container_runtime_image_digest")
    if not isinstance(runtime_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_digest) is None:
        failures.append("container_runtime_image_digest")

    action_sha = re.compile(r"^[^@]+@[0-9a-f]{40}$")
    actions = document.get("actions")
    if not isinstance(actions, list) or not actions:
        failures.append("actions")
    elif any(
        not isinstance(action, dict)
        or action.get("sha_pinned") is not True
        or not isinstance(action.get("reference"), str)
        or action_sha.fullmatch(action["reference"]) is None
        for action in actions
    ):
        failures.append("action_pins")

    evidence = [path, *admission_evidence, *external_sigstore_evidence]
    npm_audit_ref = document.get("npm_vulnerability_evidence_ref")
    npm_audit_relative = Path(npm_audit_ref) if isinstance(npm_audit_ref, str) else None
    if npm_audit_relative is None or npm_audit_relative.is_absolute() or ".." in npm_audit_relative.parts:
        failures.append("npm_vulnerability_evidence_ref")
    else:
        evidence.append(npm_audit_ref)
        try:
            npm_audit_bytes = (repo_root / npm_audit_relative).read_bytes()
            npm_audit_document = json.loads(npm_audit_bytes.decode("utf-8"))
            npm_audit_summary = verify_npm_audit_evidence(npm_audit_document, repo_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            NpmAuditEvidenceError,
            TypeError,
            ValueError,
        ):
            failures.append("npm_vulnerability_evidence")
        else:
            if (
                hashlib.sha256(npm_audit_bytes).hexdigest() != document.get("npm_vulnerability_evidence_sha256")
                or npm_audit_summary["observed_at"] != document.get("npm_vulnerability_observed_at")
                or npm_audit_summary["vulnerability_count"] != 0
                or npm_audit_summary["verdict"] != "PASS"
            ):
                failures.append("npm_vulnerability_evidence")
    python_audit_ref = document.get("python_vulnerability_evidence_ref")
    python_audit_relative = Path(python_audit_ref) if isinstance(python_audit_ref, str) else None
    if python_audit_relative is None or python_audit_relative.is_absolute() or ".." in python_audit_relative.parts:
        failures.append("python_vulnerability_evidence_ref")
    else:
        evidence.append(python_audit_ref)
        try:
            python_audit_bytes = (repo_root / python_audit_relative).read_bytes()
            python_audit_document = json.loads(python_audit_bytes.decode("utf-8"))
            python_audit_summary = verify_python_audit_evidence(python_audit_document, repo_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            PythonAuditEvidenceError,
            TypeError,
            ValueError,
        ):
            failures.append("python_vulnerability_evidence")
        else:
            if (
                hashlib.sha256(python_audit_bytes).hexdigest() != document.get("python_vulnerability_evidence_sha256")
                or python_audit_summary["observed_at"] != document.get("python_vulnerability_observed_at")
                or python_audit_summary["vulnerability_count"] != 0
                or python_audit_summary["verdict"] != "PASS"
            ):
                failures.append("python_vulnerability_evidence")
    actions_audit_ref = document.get("actions_vulnerability_evidence_ref")
    actions_audit_relative = Path(actions_audit_ref) if isinstance(actions_audit_ref, str) else None
    if actions_audit_relative is None or actions_audit_relative.is_absolute() or ".." in actions_audit_relative.parts:
        failures.append("actions_vulnerability_evidence_ref")
    else:
        evidence.append(actions_audit_ref)
        try:
            actions_audit_bytes = (repo_root / actions_audit_relative).read_bytes()
            actions_audit_document = json.loads(actions_audit_bytes.decode("utf-8"))
            actions_audit_summary = verify_actions_audit_evidence(actions_audit_document, repo_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ActionsAuditEvidenceError,
            TypeError,
            ValueError,
        ):
            failures.append("actions_vulnerability_evidence")
        else:
            if (
                hashlib.sha256(actions_audit_bytes).hexdigest() != document.get("actions_vulnerability_evidence_sha256")
                or actions_audit_summary["observed_at"] != document.get("actions_vulnerability_observed_at")
                or actions_audit_summary["vulnerability_count"] != 0
                or actions_audit_summary["verdict"] != "PASS"
            ):
                failures.append("actions_vulnerability_evidence")
    container_audit_ref = document.get("container_vulnerability_evidence_ref")
    container_audit_relative = Path(container_audit_ref) if isinstance(container_audit_ref, str) else None
    if (
        container_audit_relative is None
        or container_audit_relative.is_absolute()
        or ".." in container_audit_relative.parts
    ):
        failures.append("container_vulnerability_evidence_ref")
    else:
        evidence.append(container_audit_ref)
        try:
            container_audit_bytes = (repo_root / container_audit_relative).read_bytes()
            container_audit_document = json.loads(container_audit_bytes.decode("utf-8"))
            container_audit_summary = verify_container_audit_evidence(container_audit_document, repo_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ContainerAuditEvidenceError,
            TypeError,
            ValueError,
        ):
            failures.append("container_vulnerability_evidence")
        else:
            if (
                hashlib.sha256(container_audit_bytes).hexdigest()
                != document.get("container_vulnerability_evidence_sha256")
                or container_audit_summary["observed_at"] != document.get("container_vulnerability_observed_at")
                or container_audit_summary["base_vulnerability_count"]
                != document.get("container_base_vulnerability_count")
                or container_audit_summary["runtime_vulnerability_count"] != 0
                or container_audit_summary["runtime_image_digest"] != document.get("container_runtime_image_digest")
                or container_audit_summary["verdict"] != "PASS"
            ):
                failures.append("container_vulnerability_evidence")
    reproducibility_ref = document.get("container_reproducibility_evidence_ref")
    reproducibility_relative = Path(reproducibility_ref) if isinstance(reproducibility_ref, str) else None
    if (
        reproducibility_relative is None
        or reproducibility_relative.is_absolute()
        or ".." in reproducibility_relative.parts
    ):
        failures.append("container_reproducibility_evidence_ref")
    else:
        evidence.append(reproducibility_ref)
        try:
            reproducibility_bytes = (repo_root / reproducibility_relative).read_bytes()
            reproducibility_document = json.loads(reproducibility_bytes.decode("utf-8"))
            reproducibility_summary = verify_container_reproducibility_evidence(reproducibility_document, repo_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ContainerReproducibilityEvidenceError,
            TypeError,
            ValueError,
        ):
            failures.append("container_reproducibility_evidence")
        else:
            if (
                hashlib.sha256(reproducibility_bytes).hexdigest()
                != document.get("container_reproducibility_evidence_sha256")
                or reproducibility_summary["observed_at"] != document.get("container_reproducibility_observed_at")
                or reproducibility_summary["archive_sha256"] != document.get("container_reproducible_archive_sha256")
                or reproducibility_summary["manifest_digest"] != document.get("container_reproducible_manifest_digest")
                or reproducibility_summary["buildkit_version"]
                != document.get("container_reproducibility_buildkit_version")
                or reproducibility_summary["verdict"] != "PASS"
            ):
                failures.append("container_reproducibility_evidence")
    sbom_path = document.get("sbom_path")
    sbom_relative = Path(sbom_path) if isinstance(sbom_path, str) else None
    sbom_document: Any = None
    if sbom_relative is None or sbom_relative.is_absolute() or ".." in sbom_relative.parts:
        failures.append("sbom_path")
    else:
        evidence.append(sbom_path)
        sbom_file = repo_root / sbom_relative
        try:
            sbom_bytes = sbom_file.read_bytes()
            sbom_document = json.loads(sbom_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            failures.append("sbom_unreadable")
        else:
            if hashlib.sha256(sbom_bytes).hexdigest() != document.get("sbom_sha256"):
                failures.append("sbom_digest")
            if (
                not isinstance(sbom_document, dict)
                or sbom_document.get("bomFormat") != "CycloneDX"
                or sbom_document.get("specVersion") != "1.6"
                or not isinstance(sbom_document.get("components"), list)
                or len(sbom_document["components"]) != document.get("component_count")
            ):
                failures.append("sbom_shape")

    manifests = document.get("manifest_sha256")
    if not isinstance(manifests, dict) or not manifests:
        failures.append("manifests")
    else:
        for manifest_path in sorted(manifests, key=str):
            expected_digest = manifests[manifest_path]
            candidate = Path(manifest_path) if isinstance(manifest_path, str) else None
            if candidate is None or candidate.is_absolute() or ".." in candidate.parts:
                failures.append("manifest_path")
                continue
            evidence.append(manifest_path)
            if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
                failures.append(f"manifest_digest_shape:{manifest_path}")
                continue
            try:
                actual_digest = source_file_sha256(repo_root / candidate)
            except OSError:
                failures.append(f"manifest_missing:{manifest_path}")
                continue
            if actual_digest != expected_digest:
                failures.append(f"manifest_digest:{manifest_path}")

    if failures:
        return _result(
            "FAILED",
            "M3" if external_sigstore_verified else "M2",
            evidence,
            "supply-chain baseline failed: "
            + ", ".join(sorted(set(failures)))
            + (
                "; external GitHub OIDC Sigstore identity, Fulcio/SCT/Rekor-v1 "
                "bundle, and cross-host source reproduction independently verified"
                if external_sigstore_verified
                else ""
            ),
        )
    maturity = args.get("pass_maturity", target)
    authority_detail = (
        "a fixed local decision authority whose exact empty trust-root mirrors "
        "and hash-chain heads were verified"
        if admission_store_provisioned
        else "a fixed but unprovisioned local decision store"
    )
    return _result(
        "PASS",
        maturity,
        evidence,
        "deterministic SBOM, pinned direct inputs, immutable CI actions, manifest "
        "digests, external npm/Python/Actions advisory results, and a digest-bound "
        "Trivy base/runtime image scan with zero runtime findings verified; two "
        "same-builder cold OCI builds were byte-identical; an offline Rekor-v1-only "
        "Sigstore boundary now uses empty policy and receipt-authority registries, "
        "a verify-only Ed25519 signed-receipt gate, strict freshness/revocation "
        f"checks, and {authority_detail}; "
        + (
            "a retained GitHub OIDC/Fulcio/SCT/Rekor-v1 DSSE bundle and "
            "byte-identical Ubuntu 22.04/24.04 source artifact are independently "
            "verified; "
            if external_sigstore_verified
            else "no retained valid bundle exercises the real verifier path; "
        )
        + "an external signed receipt authority, source-native checkpoint/revocation "
        "proofs, cross-host deployable-container reproduction, independent admission "
        "deployment, WORM retention, and an external history seal remain open",
    )


_MODEL_RISK_COLON_VERSION = re.compile(r"^(?P<family>.+):v(?P<version>[0-9]+)$")
_MODEL_RISK_DASH_VERSION = re.compile(r"^(?P<family>.+)-v(?P<version>[0-9]+)$")


def _model_risk_artifact_family(artifact: Any) -> str:
    """Return the immutable logical family for versioned model-risk artifacts."""
    artifact_id = getattr(artifact, "artifact_id", "")
    if not isinstance(artifact_id, str):
        return ""
    colon = _MODEL_RISK_COLON_VERSION.fullmatch(artifact_id)
    if colon is not None:
        return colon.group("family")
    dashed = _MODEL_RISK_DASH_VERSION.fullmatch(artifact_id)
    if dashed is not None:
        return dashed.group("family")
    return artifact_id


def _model_risk_artifact_version(artifact: Any) -> int:
    artifact_id = getattr(artifact, "artifact_id", "")
    if not isinstance(artifact_id, str):
        return 0
    for pattern in (_MODEL_RISK_COLON_VERSION, _MODEL_RISK_DASH_VERSION):
        match = pattern.fullmatch(artifact_id)
        if match is not None:
            return int(match.group("version"))
    return 0


def _latest_model_risk_artifacts(artifacts: list[Any]) -> list[Any]:
    """Select one highest-version artifact per family without dropping history."""
    selected: dict[str, Any] = {}
    for artifact in artifacts:
        family = _model_risk_artifact_family(artifact)
        current = selected.get(family)
        if current is None or (
            _model_risk_artifact_version(artifact), getattr(artifact, "recorded_at", 0), getattr(artifact, "artifact_id", "")
        ) > (
            _model_risk_artifact_version(current), getattr(current, "recorded_at", 0), getattr(current, "artifact_id", "")
        ):
            selected[family] = artifact
    return sorted(selected.values(), key=lambda item: getattr(item, "artifact_id", ""))


def _model_risk_baseline_evidence(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    if not isinstance(path, str):
        return _result("UNVERIFIED", "M0", [], "checker requires an evidence path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return _result("UNVERIFIED", "M0", [], "evidence path must stay within the repository")
    evidence_path = repo_root / relative
    if not evidence_path.is_file():
        return _result("FAILED", "M1", [path], f"missing: {path}")
    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("FAILED", "M1", [path], f"invalid model-risk evidence: {exc}")
    if not isinstance(document, dict):
        return _result("FAILED", "M1", [path], "model-risk evidence must be an object")

    failures: list[str] = []
    release_numbers = []
    for record in document.get("artifacts", []):
        if not isinstance(record, dict) or record.get("artifact_type") != "ModelCard":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("model_id") != "research-agent-loop":
            continue
        version = payload.get("version")
        if isinstance(version, str) and re.fullmatch(r"v[1-9][0-9]*", version):
            release_numbers.append(int(version[1:]))
    if not release_numbers:
        failures.append("current_release_version")
        current_release_number = 1
    else:
        current_release_number = max(release_numbers)
    current_release_version = f"v{current_release_number}"
    current_runtime_report_version = f"v{current_release_number + 1}"
    current_agent_red_team_ref = (
        f"red-team-report:research-agent-boundary-{current_release_version}"
    )
    current_runtime_red_team_ref = (
        "red-team-report:research-agent-runtime-boundary-"
        f"{current_runtime_report_version}"
    )
    active_red_team_ref = document.get("red_team_report_ref")
    is_longitudinal_v2 = active_red_team_ref == current_runtime_red_team_ref
    if active_red_team_ref not in {
        current_agent_red_team_ref,
        current_runtime_red_team_ref,
    }:
        failures.append("red_team_report_ref")
    required = {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_MODEL_RISK_BASELINE",
        "current_maturity": "M2",
        "source_result_verdict": "REFUTED_NO_EDGE",
        "runtime_prompt_surface_status": ("GOVERNED_OFFLINE_BOUNDARY_NO_EXTERNAL_PROVIDER"),
        "review_identity_assurance": "PROCESS_ROLE_ONLY",
        "registry_snapshot_mode": "CANONICAL_JSON_EXPORT_FROM_EPHEMERAL_SQLITE",
        "registry_verified": True,
        "runtime_history_verified": True,
        "red_team_verdict": "PASS",
        "execution_authority_granted": False,
        "promotion_authority_granted": False,
        "credential_authority_granted": False,
        "live_trading_authority_granted": False,
    }
    failures.extend(key for key, expected in required.items() if document.get(key) != expected)
    if type(document.get("recorded_at")) is not int or document.get("recorded_at", -1) < 0:
        failures.append("recorded_at")
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    for field in (
        "dataset_sha256",
        "source_snapshot_sha256",
        "artifact_history_sha256",
        "runtime_history_sha256",
    ):
        value = document.get(field)
        if not isinstance(value, str) or digest_pattern.fullmatch(value) is None:
            failures.append(field)

    bundle_evidence: list[str] = []
    bundle_status = document.get("dataset_bundle_status")
    raw_dataset_retained = document.get("raw_dataset_retained_in_versioned_evidence")
    if bundle_status == "VERSIONED_PIT_SUBSET_BUNDLE":
        bundle_ref = document.get("dataset_ref")
        manifest_ref = document.get("dataset_bundle_manifest_ref")
        source_ref = document.get("source_dataset_ref")
        if (
            raw_dataset_retained is not True
            or not isinstance(bundle_ref, str)
            or not isinstance(manifest_ref, str)
            or not isinstance(source_ref, str)
            or type(document.get("dataset_bundle_row_count")) is not int
            or document.get("dataset_bundle_row_count", 0) <= 0
        ):
            failures.append("dataset_bundle_fields")
        else:
            try:
                bundle_manifest = verify_pit_bundle(
                    bundle_ref,
                    manifest_ref,
                    repo_root=repo_root,
                    source_db_path=None,
                )
            except DatasetBundleError:
                failures.append("dataset_bundle_verification")
            else:
                if (
                    bundle_manifest.get("bundle_sha256") != document.get("dataset_sha256")
                    or bundle_manifest.get("bundle_sha256") != document.get("dataset_bundle_sha256")
                    or bundle_manifest.get("manifest_sha256") != document.get("dataset_bundle_manifest_sha256")
                    or bundle_manifest.get("row_count") != document.get("dataset_bundle_row_count")
                    or bundle_manifest.get("source_db_sha256") != document.get("source_dataset_sha256")
                ):
                    failures.append("dataset_bundle_manifest")
                bundle_evidence.extend((bundle_ref, manifest_ref))
    elif bundle_status == "HASH_IDENTIFIED_LOCAL_ONLY":
        if (
            raw_dataset_retained is not False
            or document.get("dataset_bundle_manifest_ref") not in ("", None)
            or document.get("dataset_bundle_sha256") not in ("", None)
            or document.get("dataset_bundle_manifest_sha256") not in ("", None)
            or document.get("dataset_bundle_row_count") not in (0, None)
        ):
            failures.append("local_dataset_bundle_fields")
    else:
        failures.append("dataset_bundle_status")

    blockers = document.get("m3_blockers")
    required_blockers = {
        "NO_EXTERNAL_MODEL_PROVIDER",
        "REVIEWER_IDENTITY_PROCESS_ROLE_ONLY",
        "SOURCE_RESEARCH_RESULT_REFUTED_NO_EDGE",
    }
    longitudinal_blocker = "LONGITUDINAL_REGISTRY_HISTORY_NOT_RETAINED"
    if not is_longitudinal_v2:
        required_blockers.add(longitudinal_blocker)
    if bundle_status == "HASH_IDENTIFIED_LOCAL_ONLY":
        required_blockers.add("RAW_DATASET_NOT_RETAINED_IN_VERSIONED_EVIDENCE")
    if not isinstance(blockers, list) or not required_blockers <= set(blockers):
        failures.append("m3_blockers")
    else:
        if is_longitudinal_v2 and longitudinal_blocker in blockers:
            failures.append("stale_longitudinal_blocker")
        if (
            bundle_status == "VERSIONED_PIT_SUBSET_BUNDLE"
            and "RAW_DATASET_NOT_RETAINED_IN_VERSIONED_EVIDENCE" in blockers
        ):
            failures.append("stale_dataset_blocker")

    governance_hashes = document.get("governance_implementation_sha256")
    required_governance_paths = {
        "quantos/contracts/model_risk.py",
        "services/governance/model_risk.py",
        "services/governance/model_risk_dataset.py",
        "services/governance/model_risk_history.py",
        "services/governance/model_risk_registry.py",
        "services/intelligence/runtime.py",
        "scripts/generate_model_risk_dataset.py",
        "scripts/generate_model_risk_evidence.py",
        "scripts/update_model_risk_history.py",
    }
    governance_paths: set[str] = set()
    if not isinstance(governance_hashes, dict) or set(governance_hashes) != required_governance_paths:
        failures.append("governance_implementation_hashes")
    else:
        governance_paths = set(governance_hashes)
        for governance_path in sorted(governance_hashes):
            expected_digest = governance_hashes[governance_path]
            candidate = Path(governance_path)
            if (
                candidate.is_absolute()
                or ".." in candidate.parts
                or not isinstance(expected_digest, str)
                or digest_pattern.fullmatch(expected_digest) is None
            ):
                failures.append(f"governance_implementation_path:{governance_path}")
                continue
            try:
                actual_digest = source_file_sha256(repo_root / candidate)
            except OSError:
                failures.append(f"governance_implementation_missing:{governance_path}")
            else:
                if actual_digest != expected_digest:
                    failures.append(f"governance_implementation_digest:{governance_path}")

    snapshot = document.get("source_snapshot")
    if not isinstance(snapshot, dict):
        failures.append("source_snapshot")
    else:
        snapshot_required = {
            "asset": "fx",
            "cost_bps": 5.0,
            "single_best_name": "momentum_120",
            "single_best_oos_sharpe": -2.0389,
            "stack_gbm_folds": 24,
            "stack_gbm_oos_sharpe": -1.1173,
            "stack_ridge_folds": 24,
            "stack_ridge_oos_sharpe": -0.4035,
            "feature_rows": 123882,
            "universe_size": 18,
            "verdict": "REFUTED_NO_EDGE",
        }
        failures.extend(
            f"source_snapshot:{key}" for key, expected in snapshot_required.items() if snapshot.get(key) != expected
        )

    artifact_models = {
        "ModelCard": ModelCard,
        "PromptTemplateCard": PromptTemplateCard,
        "ToolManifest": ToolManifest,
        "OfflineEvaluation": OfflineEvaluation,
        "RedTeamReport": RedTeamReport,
        "ChallengerReview": ChallengerReview,
    }
    records = document.get("artifacts")
    loaded: list[Any] = []
    artifact_ids: set[str] = set()
    implementation_paths: set[str] = set()
    if not isinstance(records, list) or not records:
        failures.append("artifacts")
        records = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            failures.append(f"artifact_shape:{index}")
            continue
        artifact_id = record.get("artifact_id")
        artifact_type = record.get("artifact_type")
        content_hash = record.get("content_hash")
        payload = record.get("payload")
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in artifact_ids:
            failures.append(f"artifact_id:{index}")
            continue
        artifact_ids.add(artifact_id)
        model_type = artifact_models.get(artifact_type)
        if model_type is None or not isinstance(payload, dict):
            failures.append(f"artifact_type:{artifact_id}")
            continue
        if (
            not isinstance(content_hash, str)
            or digest_pattern.fullmatch(content_hash) is None
            or stable_hash(payload) != content_hash
        ):
            failures.append(f"artifact_hash:{artifact_id}")
            continue
        try:
            artifact = model_type.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            failures.append(f"artifact_contract:{artifact_id}")
            continue
        if artifact.artifact_id != artifact_id or artifact.content_hash != content_hash:
            failures.append(f"artifact_identity:{artifact_id}")
            continue
        loaded.append(artifact)
        if isinstance(artifact, (ModelCard, ToolManifest)):
            implementation_paths.add(artifact.implementation_ref)
        if (
            isinstance(artifact, (ModelCard, ToolManifest))
            and artifact.version == current_release_version
        ):
            candidate = Path(artifact.implementation_ref)
            if candidate.is_absolute() or ".." in candidate.parts:
                failures.append(f"implementation_path:{artifact_id}")
                continue
            try:
                actual = source_file_sha256(repo_root / candidate)
            except OSError:
                failures.append(f"implementation_missing:{artifact.implementation_ref}")
            else:
                if actual != artifact.implementation_sha256:
                    failures.append(
                        f"implementation_digest:{artifact.implementation_ref}"
                    )

    artifact_count = document.get("artifact_count")
    if type(artifact_count) is not int or artifact_count != len(records):
        failures.append("artifact_count")
    actual_counts: dict[str, int] = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("artifact_type"), str):
            artifact_type = record["artifact_type"]
            actual_counts[artifact_type] = actual_counts.get(artifact_type, 0) + 1
    if document.get("artifact_type_counts") != actual_counts:
        failures.append("artifact_type_counts")
    if [
        record.get("artifact_id")
        for record in records
        if isinstance(record, dict)
    ] != sorted(artifact_ids):
        failures.append("artifact_order")
    artifact_previous_hash = ""
    for sequence, record in enumerate(records, start=1):
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            continue
        artifact_previous_hash = stable_hash(
            {
                "artifact": record["payload"],
                "previous_hash": artifact_previous_hash,
                "sequence": sequence,
            }
        )
    if artifact_previous_hash != document.get("artifact_history_sha256"):
        failures.append("artifact_history_hash")

    model_cards = [item for item in loaded if isinstance(item, ModelCard)]
    prompt_cards = [item for item in loaded if isinstance(item, PromptTemplateCard)]
    tool_manifests = [item for item in loaded if isinstance(item, ToolManifest)]
    evaluations = [item for item in loaded if isinstance(item, OfflineEvaluation)]
    red_team_reports = [item for item in loaded if isinstance(item, RedTeamReport)]
    challenger_reviews = [item for item in loaded if isinstance(item, ChallengerReview)]
    allowed_release_versions = {
        f"v{release_number}"
        for release_number in range(1, current_release_number + 1)
    }
    if any(
        artifact.version not in allowed_release_versions
        for artifact in (*model_cards, *prompt_cards, *tool_manifests, *evaluations)
    ):
        failures.append("unknown_artifact_release")
    active_model_cards = [
        card for card in model_cards if card.version == current_release_version
    ]
    active_prompt_cards = [
        prompt for prompt in prompt_cards if prompt.version == current_release_version
    ]
    active_tool_manifests = [
        manifest
        for manifest in tool_manifests
        if manifest.version == current_release_version
    ]
    active_evaluations = [
        evaluation
        for evaluation in evaluations
        if evaluation.version == current_release_version
    ]
    active_challenger_reviews = [
        review
        for review in challenger_reviews
        if review.review_id.endswith(f"-{current_release_version}")
    ]
    runtime_models = [
        card
        for card in active_model_cards
        if card.model_ref == f"research-agent-loop@{current_release_version}"
    ]
    non_runtime_models = [
        card
        for card in active_model_cards
        if card.model_ref != f"research-agent-loop@{current_release_version}"
    ]
    if (
        len(active_model_cards) != 4
        or len(active_prompt_cards) != 1
        or len(active_tool_manifests) != 1
        or len(active_evaluations) != 3
        or len(active_challenger_reviews) != 2
        or len(runtime_models) != 1
    ):
        failures.append("active_release_artifact_counts")
    if len(active_prompt_cards) != 1 or len(runtime_models) != 1:
        failures.append("prompt_lineage")
    else:
        prompt = active_prompt_cards[0]
        runtime_model = runtime_models[0]
        if (
            prompt.prompt_ref
            != f"research-evaluation@{current_release_version}"
            or prompt.model_ref != runtime_model.model_ref
            or runtime_model.prompt_lineage_status.value != "REGISTERED"
            or runtime_model.prompt_template_ref != prompt.prompt_ref
            or runtime_model.tool_manifest_ref != prompt.tool_manifest_ref
            or prompt.status.value != "RESEARCH_ONLY"
        ):
            failures.append("prompt_lineage")
        runtime_path = repo_root / "services/intelligence/runtime.py"
        try:
            runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
            template_value = next(
                ast.literal_eval(node.value)
                for node in runtime_tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and (
                    any(
                        isinstance(target_node, ast.Name) and target_node.id == "RESEARCH_EVALUATION_TEMPLATE"
                        for target_node in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                )
            )
        except (OSError, SyntaxError, StopIteration, ValueError, TypeError):
            failures.append("runtime_prompt_source")
        else:
            if (
                not isinstance(template_value, str)
                or hashlib.sha256(template_value.encode("utf-8")).hexdigest() != prompt.template_sha256
            ):
                failures.append("runtime_prompt_digest")
    if any(
        card.prompt_lineage_status.value != "NO_RUNTIME_LLM_PROMPT_SURFACE" or card.prompt_template_ref
        for card in non_runtime_models
    ):
        failures.append("non_runtime_prompt_lineage")
    if len(active_tool_manifests) != 1:
        failures.append("tool_manifest_count")
    else:
        prohibited = set(active_tool_manifests[0].prohibited_actions)
        if (
            not {
                "APPROVE",
                "RESET_KILL_SWITCH",
                "LIVE_TRADE",
                "ACCESS_CREDENTIALS",
            }
            <= prohibited
        ):
            failures.append("tool_manifest_boundaries")

    runtime_events = document.get("runtime_events")
    previous_event_hash = ""
    current_baseline_invocation = (
        f"model-risk-baseline-runtime-{current_release_version}"
    )
    current_runtime_invocation = (
        f"model-risk-runtime-red-team-{current_runtime_report_version}"
    )
    current_profile = [current_baseline_invocation]
    if is_longitudinal_v2:
        current_profile.append(current_runtime_invocation)
    allowed_profiles = [current_profile]
    if is_longitudinal_v2:
        allowed_profiles.extend(
            [
                invocation_id
                for release_number in range(first_release, current_release_number)
                for invocation_id in (
                    f"model-risk-baseline-runtime-v{release_number}",
                    f"model-risk-runtime-red-team-v{release_number + 1}",
                )
            ]
            + current_profile
            for first_release in range(1, current_release_number)
        )
    runtime_ids = (
        [event.get("invocation_id") for event in runtime_events]
        if isinstance(runtime_events, list)
        and all(isinstance(event, dict) for event in runtime_events)
        else []
    )
    if runtime_ids not in allowed_profiles:
        failures.append("runtime_event_profile")
    expected_runtime_invocations = len(runtime_ids)
    if (
        not isinstance(runtime_events, list)
        or document.get("runtime_invocation_count") != expected_runtime_invocations
    ):
        failures.append("runtime_events")
        runtime_events = []
    lineage_by_invocation = {}
    for release_number in range(1, current_release_number + 1):
        release = f"v{release_number}"
        lineage = (
            f"research-agent-loop@{release}",
            f"research-evaluation@{release}",
            f"research-agent-tools@{release}",
        )
        lineage_by_invocation[f"model-risk-baseline-runtime-{release}"] = lineage
        lineage_by_invocation[
            f"model-risk-runtime-red-team-v{release_number + 1}"
        ] = lineage
    seen_runtime_ids: set[str] = set()
    for expected_sequence, event in enumerate(runtime_events, start=1):
        if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
            failures.append("runtime_event_shape")
            continue
        payload = event["payload"]
        try:
            invocation = RuntimeInvocation.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            failures.append("runtime_event_contract")
            continue
        if invocation.invocation_id in seen_runtime_ids:
            failures.append("runtime_event_duplicate_identity")
        seen_runtime_ids.add(invocation.invocation_id)
        expected_lineage = lineage_by_invocation.get(invocation.invocation_id)
        if (
            event.get("sequence") != expected_sequence
            or event.get("invocation_id") != invocation.invocation_id
            or event.get("recorded_at") != invocation.recorded_at
            or event.get("model_ref") != invocation.model_ref
            or event.get("prompt_ref") != invocation.prompt_ref
            or event.get("tool_manifest_ref") != invocation.tool_manifest_ref
            or event.get("action") != invocation.action
            or event.get("previous_hash") != previous_event_hash
            or invocation.outcome != RuntimeInvocationOutcome.ALLOWED
            or expected_lineage is None
            or (
                invocation.model_ref,
                invocation.prompt_ref,
                invocation.tool_manifest_ref,
            )
            != expected_lineage
            or invocation.action != "EVALUATE"
        ):
            failures.append("runtime_event_lineage")
        expected_event_hash = stable_hash({"previous_hash": previous_event_hash, "invocation": payload})
        if event.get("event_hash") != expected_event_hash:
            failures.append("runtime_event_hash")
        previous_event_hash = expected_event_hash
    if previous_event_hash != document.get("runtime_history_sha256"):
        failures.append("runtime_history_hash")
    if runtime_events and len(runtime_models) == 1 and len(active_prompt_cards) == 1 and len(active_tool_manifests) == 1 and (
        runtime_events[-1].get("model_ref") != runtime_models[0].model_ref
        or runtime_events[-1].get("prompt_ref") != active_prompt_cards[0].prompt_ref
        or runtime_events[-1].get("tool_manifest_ref") != active_tool_manifests[0].manifest_ref
    ):
        failures.append("runtime_active_lineage")

    evaluation_refs = {evaluation.evaluation_ref for evaluation in evaluations}
    model_refs = {card.model_ref for card in model_cards}
    primary_values: list[float] = []
    for evaluation in active_evaluations:
        metrics = {metric.name: metric.value for metric in evaluation.metrics}
        value = metrics.get("oos_sharpe_net_5bps")
        if value is None or value >= 0:
            failures.append(f"negative_oos_metric:{evaluation.evaluation_id}")
        else:
            primary_values.append(value)
        if evaluation.verdict.value != "REFUTED_NO_EDGE":
            failures.append(f"evaluation_verdict:{evaluation.evaluation_id}")
        if evaluation.model_ref not in model_refs:
            failures.append(f"evaluation_model_ref:{evaluation.evaluation_id}")
        if evaluation.dataset_sha256 != document.get("dataset_sha256"):
            failures.append(f"evaluation_dataset_digest:{evaluation.evaluation_id}")
        if evaluation.source_artifact_sha256 != document.get("source_snapshot_sha256"):
            failures.append(f"evaluation_snapshot_digest:{evaluation.evaluation_id}")
    active_primary_values: list[float] = []
    for evaluation in active_evaluations:
        metrics = {metric.name: metric.value for metric in evaluation.metrics}
        value = metrics.get("oos_sharpe_net_5bps")
        if value is not None:
            active_primary_values.append(value)
    if sorted(round(value, 4) for value in active_primary_values) != [-2.0389, -1.1173, -0.4035]:
        failures.append("negative_oos_values")

    base_required_cases = {
        "human-actor-constructor",
        "agent-approve",
        "agent-reset-kill-switch",
        "agent-request-human-approval",
    }
    runtime_required_cases = {
        *base_required_cases,
        "runtime-action-override",
        "runtime-sensitive-output",
        "runtime-forbidden-tool-action",
        "external-provider-unverified-receipt",
        "external-provider-request-digest-tamper",
        "external-provider-response-digest-tamper",
        "external-provider-sensitive-output",
    }
    legacy_runtime_required_cases = {
        *base_required_cases,
        "runtime-action-override",
        "runtime-sensitive-output",
        "runtime-forbidden-tool-action",
    }
    expected_red_team_cases = {}
    expected_red_team_lineage = {}
    for release_number in range(1, current_release_number + 1):
        release = f"v{release_number}"
        boundary_ref = f"red-team-report:research-agent-boundary-{release}"
        runtime_ref = (
            "red-team-report:research-agent-runtime-boundary-"
            f"v{release_number + 1}"
        )
        lineage = (
            f"research-agent-loop@{release}",
            f"research-agent-tools@{release}",
        )
        expected_red_team_cases[boundary_ref] = (base_required_cases,)
        expected_red_team_cases[runtime_ref] = (
            (runtime_required_cases,)
            # External-provider receipt attacks entered the retained lineage at
            # runtime report v8 (model release v7). Earlier reports are valid
            # either as their retained pre-provider artifact or as a regenerated
            # report under the stronger current harness.
            if release_number >= 7
            else (legacy_runtime_required_cases, runtime_required_cases)
        )
        expected_red_team_lineage[boundary_ref] = lineage
        expected_red_team_lineage[runtime_ref] = lineage
    reports_by_id = {report.artifact_id: report for report in red_team_reports}
    required_current_reports = {current_agent_red_team_ref}
    if is_longitudinal_v2:
        required_current_reports.add(current_runtime_red_team_ref)
    if (
        not required_current_reports <= set(reports_by_id)
        or not set(reports_by_id) <= set(expected_red_team_cases)
    ):
        failures.append("red_team_report_count")
    for report_ref, report in reports_by_id.items():
        required_case_options = expected_red_team_cases.get(report_ref, ())
        observed_required_cases = set(report.required_case_ids)
        observed_cases = {case.case_id for case in report.cases}
        expected_lineage = expected_red_team_lineage.get(report_ref)
        if (
            report.verdict != RedTeamVerdict.PASS
            or not any(
                observed_required_cases == required_cases
                and observed_cases == required_cases
                for required_cases in required_case_options
            )
            or any(case.boundary_breached for case in report.cases)
            or expected_lineage is None
            or (report.model_ref, report.tool_manifest_ref) != expected_lineage
        ):
            failures.append("red_team_cases")

    expected_review_refs = sorted(review.artifact_id for review in challenger_reviews)
    if expected_review_refs != document.get("challenger_review_refs"):
        failures.append("challenger_review_refs")
    if len(active_challenger_reviews) != 2 or any(
        review.verdict != ChallengerVerdict.REJECT_ABSOLUTE_FLOOR
        or review.identity_assurance != ReviewIdentityAssurance.PROCESS_ROLE_ONLY
        or review.model_owner_group == review.reviewer_group
        or review.incumbent_evaluation_ref not in evaluation_refs
        or review.challenger_evaluation_ref not in evaluation_refs
        for review in active_challenger_reviews
    ):
        failures.append("challenger_reviews")
    expected_challenger_verdicts = [
        review.verdict.value
        for review in sorted(active_challenger_reviews, key=lambda review: review.artifact_id)
    ]
    if document.get("challenger_verdicts") != expected_challenger_verdicts:
        failures.append("challenger_verdicts")

    dataset_ref = document.get("dataset_ref")
    if isinstance(dataset_ref, str):
        dataset_relative = Path(dataset_ref)
        if dataset_relative.is_absolute() or ".." in dataset_relative.parts:
            failures.append("dataset_ref")
        elif (repo_root / dataset_relative).is_file():
            actual_dataset = hashlib.sha256((repo_root / dataset_relative).read_bytes()).hexdigest()
            if actual_dataset != document.get("dataset_sha256"):
                failures.append("dataset_digest")
    else:
        failures.append("dataset_ref")

    snapshot_ref = document.get("source_snapshot_ref")
    if isinstance(snapshot_ref, str):
        snapshot_relative = Path(snapshot_ref)
        if snapshot_relative.is_absolute() or ".." in snapshot_relative.parts:
            failures.append("source_snapshot_ref")
        elif (repo_root / snapshot_relative).is_file():
            actual_snapshot = hashlib.sha256((repo_root / snapshot_relative).read_bytes()).hexdigest()
            if actual_snapshot != document.get("source_snapshot_sha256"):
                failures.append("source_snapshot_digest")
    else:
        failures.append("source_snapshot_ref")

    source_dataset_ref = document.get("source_dataset_ref")
    if isinstance(source_dataset_ref, str):
        source_dataset_relative = Path(source_dataset_ref)
        if source_dataset_relative.is_absolute() or ".." in source_dataset_relative.parts:
            failures.append("source_dataset_ref")
        elif (
            bundle_status == "HASH_IDENTIFIED_LOCAL_ONLY"
            and (repo_root / source_dataset_relative).is_file()
        ):
            actual_source_dataset = hashlib.sha256((repo_root / source_dataset_relative).read_bytes()).hexdigest()
            if actual_source_dataset != document.get("source_dataset_sha256"):
                failures.append("source_dataset_digest")
    else:
        failures.append("source_dataset_ref")

    history_evidence: list[str] = []
    history_detail = "longitudinal multi-revision history remains open"
    history_path = args.get("history_path")
    if history_path is None:
        if is_longitudinal_v2:
            failures.append("revision_history_required_for_v2")
    else:
        candidate = Path(history_path) if isinstance(history_path, str) else None
        if candidate is None or candidate.is_absolute() or ".." in candidate.parts:
            failures.append("revision_history_path")
        else:
            history_evidence.append(history_path)
            try:
                history_document = json.loads((repo_root / candidate).read_text(encoding="utf-8"))
                history_summary = validate_model_risk_history(history_document)
            except (
                OSError,
                json.JSONDecodeError,
                ModelRiskHistoryError,
                TypeError,
                ValueError,
            ):
                failures.append("revision_history")
            else:
                if not history_matches_evidence(history_document, document):
                    failures.append("revision_history_current")
                elif history_summary["revision_count"] == 1:
                    if is_longitudinal_v2:
                        failures.append("revision_history_longitudinal")
                    history_detail = (
                        "a current tamper-evident single-revision history is verified; "
                        "longitudinal multi-revision history remains open"
                    )
                else:
                    history_detail = "a current tamper-evident multi-revision history is verified"

    evidence = [path, *bundle_evidence, *history_evidence]
    for verification_path in args.get("verification_paths", []):
        if not isinstance(verification_path, str):
            failures.append("verification_path")
            continue
        candidate = Path(verification_path)
        if candidate.is_absolute() or ".." in candidate.parts or not (repo_root / candidate).is_file():
            failures.append(f"verification_path:{verification_path}")
            continue
        evidence.append(verification_path)
    evidence.extend(sorted(implementation_paths))
    evidence.extend(sorted(governance_paths))
    evidence = list(dict.fromkeys(evidence))

    maturity_profile = args.get("maturity_profile", "OFFLINE_BASELINE_M2")
    allowed_maturity_profiles = {
        "OFFLINE_BASELINE_M2",
        "EXTERNAL_PROVIDER_RUNTIME_M3",
        "EXTERNAL_REVIEW_M3",
    }
    if maturity_profile not in allowed_maturity_profiles:
        failures.append("maturity_profile")
    external_provider_summary: dict[str, Any] | None = None
    external_provider_path = args.get("external_provider_path")
    if external_provider_path is None:
        if maturity_profile != "OFFLINE_BASELINE_M2":
            failures.append("external_provider_path")
    elif not isinstance(external_provider_path, str):
        failures.append("external_provider_path")
    else:
        candidate = Path(external_provider_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            failures.append("external_provider_path")
        else:
            external_evidence_file = repo_root / candidate
            if external_evidence_file.is_file():
                evidence.append(external_provider_path)
                try:
                    external_document = json.loads(
                        external_evidence_file.read_text(encoding="utf-8")
                    )
                    external_provider_summary = (
                        verify_external_model_provider_evidence(
                            external_document,
                            repo_root=repo_root,
                        )
                    )
                except (
                    OSError,
                    json.JSONDecodeError,
                    ExternalModelProviderEvidenceError,
                    TypeError,
                    ValueError,
                ):
                    failures.append("external_provider_evidence")
                else:
                    evidence.extend(external_provider_summary["evidence_paths"])
                    evidence = list(dict.fromkeys(evidence))

    if failures:
        return _result(
            "FAILED",
            "M1",
            evidence,
            "model-risk baseline failed: " + ", ".join(sorted(set(failures))),
        )
    dataset_detail = (
        "a deterministic versioned PIT evaluation subset is retained and verified"
        if bundle_status == "VERSIONED_PIT_SUBSET_BUNDLE"
        else "raw-data retention remains open"
    )
    maturity = args.get("pass_maturity", target)
    if (
        maturity_profile == "EXTERNAL_PROVIDER_RUNTIME_M3"
        and external_provider_summary is not None
        and external_provider_summary.get("current_maturity") == "M3"
        and external_provider_summary.get("external_provider_exercised") is True
    ):
        maturity = "M3"
        provider_detail = (
            "a registered external model provider completed a redacted live HTTPS "
            "probe through the governed runtime; the provider body is TLS-authenticated "
            "but unsigned, and strong external reviewer identity remains open"
        )
    elif (
        maturity_profile == "EXTERNAL_REVIEW_M3"
        and external_provider_summary is not None
    ):
        provider_detail = (
            "a registered external provider live HTTPS probe is verified, but challenger "
            "reviews remain process-role-only without a strong external reviewer"
        )
    else:
        provider_detail = (
            "a switchable external-provider boundary and verifier are implemented, but "
            "no passing live HTTPS probe is retained and strong reviewer identity remains open"
            if maturity_profile != "OFFLINE_BASELINE_M2"
            else "an external model provider and strong reviewer identity remain open"
        )
    return _result(
        "PASS",
        maturity,
        evidence,
        "immutable model/tool/evaluation lineage and real agent-boundary red-team "
        "evidence verified; both challengers have negative OOS Sharpe and fail the "
        "absolute floor; a registered offline prompt/tool runtime and tamper-evident "
        f"invocation chain are verified; {dataset_detail}; {history_detail}; "
        f"{provider_detail}",
    )


def _strong_identity_baseline_evidence(
    repo_root: Path,
    args: dict[str, Any],
    target: str,
    _context: AssessmentContext,
) -> dict[str, Any]:
    path = args.get("path")
    if not isinstance(path, str):
        return _result("UNVERIFIED", "M0", [], "checker requires an evidence path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return _result("UNVERIFIED", "M0", [], "evidence path must stay within the repository")
    evidence_path = repo_root / relative
    if not evidence_path.is_file():
        return _result("FAILED", "M1", [path], f"missing: {path}")
    try:
        raw_evidence = evidence_path.read_text(encoding="utf-8")
        document = json.loads(raw_evidence)
    except (OSError, json.JSONDecodeError) as exc:
        return _result("FAILED", "M1", [path], f"invalid strong-identity evidence: {exc}")
    if not isinstance(document, dict):
        return _result("FAILED", "M1", [path], "strong-identity evidence must be an object")

    failures: list[str] = []
    required = {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_STRONG_IDENTITY_BASELINE",
        "current_maturity": "M3",
        "authentication_profile": "OIDC_ASYMMETRIC_MTLS_MFA",
        "allowed_algorithms": ["EdDSA", "RS256"],
        "token_binding_standard": "RFC8705_CNF_X5T_S256",
        "actor_source": "OIDC_ISSUER_AND_IMMUTABLE_SUBJECT",
        "valid_identity_path_passed": True,
        "application_authorization_valid_path_passed": True,
        "application_authorization_registry_status": ("EMPTY_DEFAULT_FAIL_CLOSED_GENERATION_0"),
        "application_authorization_decision_authority_status": ("LOCAL_SINGLE_SQLITE_REGISTRY_DECISION_CAS_UNSEALED"),
        "application_authorization_principal_classes": ["HUMAN", "WORKLOAD"],
        "application_authorization_token_role_mode": ("TOKEN_AND_APPLICATION_GRANT_INTERSECTION"),
        "three_party_identity_separation_passed": True,
        "all_attack_cases_passed": True,
        "legacy_shared_token_accepted": False,
        "self_asserted_operator_accepted": False,
        "external_oidc_deployment_status": "NOT_PROVIDED",
        "trusted_mtls_deployment_status": "NOT_PROVIDED",
        "browser_pkce_or_bff_status": ("AUTHORIZATION_CODE_PKCE_S256_IMPLEMENTED_EXTERNAL_IDP_NOT_PROVIDED"),
        "browser_pkce_transaction_storage": ("VERSIONED_SESSION_STORAGE_NO_ACCESS_TOKEN"),
        "browser_access_token_storage": "IN_MEMORY_ONLY",
        "browser_pkce_client_secret_present": False,
        "browser_pkce_one_time_state_nonce": True,
        "browser_pkce_max_transaction_age_seconds": 600,
        "browser_pkce_invariants_verified": True,
        "external_key_custody_status": "NOT_PROVIDED",
        "identity_lifecycle_evidence_status": (
            "LOCAL_REVOCATION_TOKEN_EPOCH_ACCESS_REVIEW_ENFORCED_EXTERNAL_OPERATIONS_NOT_PROVIDED"
        ),
        "ai_human_identity_exclusion_status": ("LOCAL_HUMAN_WORKLOAD_CLASS_ENFORCED_NOT_EXTERNALLY_PROVEN"),
        "live_trading_authority_granted": False,
        "credential_authority_granted": False,
        "order_submission_authority_granted": False,
    }
    failures.extend(key for key, expected in required.items() if document.get(key) != expected)
    if type(document.get("recorded_at")) is not int or document.get("recorded_at", -1) < 0:
        failures.append("recorded_at")
    if "PRIVATE KEY" in raw_evidence or "legacy-shared-token" in raw_evidence:
        failures.append("sensitive_material")

    expected_roles = {
        "PROMOTION_READER",
        "PROMOTION_REQUESTER",
        "PROMOTION_REVIEWER",
        "PROMOTION_APPROVER",
        "PROMOTION_EXECUTOR",
    }
    roles = document.get("governance_roles")
    if not isinstance(roles, list) or set(roles) != expected_roles:
        failures.append("governance_roles")

    expected_attacks = {
        "wrong-signature": "INVALID_SIGNATURE",
        "symmetric-algorithm-confusion": "ALGORITHM_NOT_ALLOWED",
        "wrong-issuer": "INVALID_ISSUER",
        "wrong-audience": "INVALID_AUDIENCE",
        "expired-token": "TOKEN_EXPIRED",
        "stale-authentication": "STALE_AUTHENTICATION",
        "missing-mfa": "AMR_REQUIRED",
        "wrong-certificate-binding": "CERTIFICATE_BINDING_MISMATCH",
        "reader-role-escalation": "ROLE_REQUIRED",
        "token-role-self-enrollment": "PRINCIPAL_NOT_REGISTERED",
        "token-role-self-escalation": "ROLE_NOT_GRANTED",
        "revoked-application-principal": "PRINCIPAL_REVOKED",
        "expired-application-access-review": "ACCESS_REVIEW_EXPIRED",
        "token-before-application-epoch": "TOKEN_ISSUED_BEFORE_AUTHORIZATION",
        "workload-human-governance-action": "WORKLOAD_HUMAN_ACTION_FORBIDDEN",
        "untrusted-certificate-header": "MISSING_VERIFIED_CLIENT_CERTIFICATE",
        "legacy-shared-secret": "MALFORMED_TOKEN",
        "wrong-token-type": "INVALID_TOKEN_TYPE",
        "duplicate-human-subject": "",
    }
    attacks = document.get("attack_cases")
    if not isinstance(attacks, list) or len(attacks) != len(expected_attacks):
        failures.append("attack_cases")
    else:
        seen_attacks: set[str] = set()
        for attack in attacks:
            if not isinstance(attack, dict):
                failures.append("attack_cases")
                continue
            case_id = attack.get("case_id")
            if not isinstance(case_id, str) or case_id in seen_attacks:
                failures.append("attack_case_ids")
                continue
            seen_attacks.add(case_id)
            expected_code = expected_attacks.get(case_id)
            if expected_code is None:
                failures.append(f"attack_case_unknown:{case_id}")
                continue
            if (
                attack.get("expected_outcome") != "DENIED"
                or attack.get("actual_outcome") != "DENIED"
                or attack.get("passed") is not True
                or attack.get("error_code") != expected_code
            ):
                failures.append(f"attack_case:{case_id}")
        if seen_attacks != set(expected_attacks):
            failures.append("attack_cases")

    three_party = document.get("three_party_case")
    if (
        not isinstance(three_party, dict)
        or three_party.get("case_id") != "three-distinct-human-subjects"
        or three_party.get("expected_outcome") != "ALLOWED"
        or three_party.get("actual_outcome") != "ALLOWED"
        or three_party.get("passed") is not True
    ):
        failures.append("three_party_case")

    blockers = document.get("m4_blockers")
    required_blockers = {
        "EXTERNAL_OIDC_ISSUER_OPERATIONAL_EVIDENCE_MISSING",
        "TRUSTED_MTLS_TERMINATOR_DEPLOYMENT_EVIDENCE_MISSING",
        "EXTERNAL_SIGNING_KEY_CUSTODY_AND_ROTATION_EVIDENCE_MISSING",
        "EXTERNAL_IDENTITY_LIFECYCLE_AND_ACCESS_REVIEW_EVIDENCE_MISSING",
        "AI_WORKLOAD_HUMAN_IDENTITY_EXCLUSION_NOT_EXTERNALLY_PROVEN",
    }
    if not isinstance(blockers, list) or set(blockers) != required_blockers:
        failures.append("m4_blockers")

    required_implementations = {
        "quantos/contracts/identity.py",
        "services/governance/strong_identity.py",
        "services/governance/identity_authorization.py",
        "services/governance/identity_authorization_store.py",
        "config/identity_authorization_registry.json",
        "services/promotion_api/main.py",
        "apps/research-console/src/lib/api/promotion-api.ts",
        "apps/research-console/src/lib/api/governance-pkce.ts",
        "apps/research-console/src/pages/PromotionQueue.tsx",
        "scripts/generate_strong_identity_evidence.py",
    }
    implementation_hashes = document.get("implementation_sha256")
    evidence = [path]
    if not isinstance(implementation_hashes, dict) or set(implementation_hashes) != required_implementations:
        failures.append("implementation_hashes")
    else:
        for implementation_path in sorted(implementation_hashes):
            expected_digest = implementation_hashes[implementation_path]
            candidate = Path(implementation_path)
            evidence.append(implementation_path)
            if (
                candidate.is_absolute()
                or ".." in candidate.parts
                or not isinstance(expected_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
            ):
                failures.append(f"implementation_path:{implementation_path}")
                continue
            try:
                actual_digest = source_file_sha256(repo_root / candidate)
            except OSError:
                failures.append(f"implementation_missing:{implementation_path}")
            else:
                if actual_digest != expected_digest:
                    failures.append(f"implementation_digest:{implementation_path}")

    required_verification = {
        "tests/governance/test_strong_identity.py",
        "tests/governance/test_identity_authorization.py",
        "tests/governance/test_identity_authorization_store.py",
        "tests/governance/test_promotion_api_boundary.py",
        "tests/governance/test_promotion_api_endpoints.py",
        "apps/research-console/src/lib/api/governance-pkce.test.ts",
    }
    verification_paths = document.get("verification_paths")
    if not isinstance(verification_paths, list) or set(verification_paths) != required_verification:
        failures.append("verification_paths")
    else:
        for verification_path in sorted(verification_paths):
            candidate = Path(verification_path)
            evidence.append(verification_path)
            if candidate.is_absolute() or ".." in candidate.parts or not (repo_root / candidate).is_file():
                failures.append(f"verification_path:{verification_path}")

    dependency_versions = document.get("dependency_versions")
    if dependency_versions != {"PyJWT": "2.13.0", "cryptography": "49.0.0"}:
        failures.append("dependency_versions")
    for manifest_path, needles in {
        "requirements.txt": ("PyJWT==2.13.0", "cryptography==49.0.0"),
        "pyproject.toml": ("PyJWT==2.13.0", "cryptography==49.0.0"),
    }.items():
        try:
            manifest = (repo_root / manifest_path).read_text(encoding="utf-8")
        except OSError:
            failures.append(f"dependency_manifest_missing:{manifest_path}")
            continue
        evidence.append(manifest_path)
        if any(needle not in manifest for needle in needles):
            failures.append(f"dependency_pin:{manifest_path}")

    try:
        promotion_source = (repo_root / "services/promotion_api/main.py").read_text(encoding="utf-8")
        verifier_source = (repo_root / "services/governance/strong_identity.py").read_text(encoding="utf-8")
        authorization_source = (repo_root / "services/governance/identity_authorization.py").read_text(encoding="utf-8")
        authorization_store_source = (repo_root / "services/governance/identity_authorization_store.py").read_text(
            encoding="utf-8"
        )
        default_authorization_registry = json.loads(
            (repo_root / "config/identity_authorization_registry.json").read_text(encoding="utf-8")
        )
        frontend_source = (repo_root / "apps/research-console/src/lib/api/promotion-api.ts").read_text(encoding="utf-8")
        pkce_source = (repo_root / "apps/research-console/src/lib/api/governance-pkce.ts").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        failures.append("load_bearing_source")
    else:
        legacy_markers = (
            "X-Gov-Token",
            "X-Operator",
            "_load_or_create_token",
            "token_urlsafe",
        )
        if any(marker in promotion_source for marker in legacy_markers):
            failures.append("legacy_promotion_identity")
        if any(marker in frontend_source for marker in legacy_markers[:2]):
            failures.append("legacy_frontend_identity")
        if not {
            "Authorization",
            "extract_verified_client_certificate",
            "PROMOTION_APPROVER",
            "PROMOTION_EXECUTOR",
            "evaluate_authorization",
            "IdentityAuthorizationStore",
        } <= set(re.findall(r"[A-Za-z_]+", promotion_source)):
            failures.append("promotion_identity_markers")
        if not all(
            marker in authorization_source
            for marker in (
                "tokens_valid_after_ms",
                "access_review_expires_at_ms",
                "WORKLOAD_HUMAN_ACTION_FORBIDDEN",
                "TOKEN_ROLE_REQUIRED",
            )
        ):
            failures.append("application_authorization_markers")
        if not all(
            marker in authorization_store_source
            for marker in (
                "BEGIN IMMEDIATE",
                "append_registry_snapshot",
                "expected_registry_head",
                "identity_authorization_events",
            )
        ):
            failures.append("application_authorization_store_markers")
        if default_authorization_registry != {
            "schema_version": 1,
            "readiness": "L2_DRY_RUN_ONLY",
            "generation": 0,
            "principals": {},
        }:
            failures.append("default_application_authorization_registry")
        if not all(
            marker in verifier_source
            for marker in (
                "jwt.decode",
                "x5t#S256",
                "client_cert_chain_verified",
                "required_amr_values",
            )
        ):
            failures.append("verifier_markers")
        if "Authorization" not in frontend_source or "Bearer" not in frontend_source:
            failures.append("frontend_bearer_identity")
        pkce_markers = (
            "code_challenge_method",
            "S256",
            "getRandomValues",
            "subtle.digest",
            "response_type",
            "state",
            "nonce",
            "PKCE_TRANSACTION_STORAGE_KEY",
            "MAX_TRANSACTION_AGE_MS",
            "storage.removeItem",
            "response = await runtime.fetch",
        )
        if not all(marker in pkce_source for marker in pkce_markers):
            failures.append("pkce_markers")
        if "client_secret" in pkce_source or "localStorage" in pkce_source:
            failures.append("pkce_browser_secret_storage")
        if "sessionStorage" in frontend_source or "localStorage" in frontend_source:
            failures.append("frontend_access_token_storage")
        try:
            transaction_removed_at = pkce_source.index("storage.removeItem")
            token_exchange_at = pkce_source.index("response = await runtime.fetch")
        except ValueError:
            failures.append("pkce_one_time_transaction")
        else:
            if transaction_removed_at >= token_exchange_at:
                failures.append("pkce_one_time_transaction")

    evidence = list(dict.fromkeys(evidence))
    if failures:
        return _result(
            "FAILED",
            "M1",
            evidence,
            "strong-identity baseline failed: " + ", ".join(sorted(set(failures))),
        )
    maturity = args.get("pass_maturity", target)
    return _result(
        "PASS",
        maturity,
        evidence,
        "asymmetric OIDC verification, MFA, RFC 8705 mTLS binding, immutable "
        "subject identity, application-owned lifecycle authorization, durable "
        "authorization decisions, and three-party separation are load-bearing; "
        "browser Authorization Code + PKCE S256 is implemented with one-time state/nonce "
        "and memory-only access tokens; external OIDC, trusted TLS deployment, managed "
        "key custody, external identity lifecycle operations, and independent AI "
        "identity-exclusion evidence remain open",
    )


def _result(status: str, maturity: str, evidence: list[str], detail: str) -> dict[str, Any]:
    return {"status": status, "current_maturity": maturity, "evidence": evidence, "detail": detail}


CHECKERS: dict[
    str,
    Callable[[Path, dict[str, Any], str, AssessmentContext], dict[str, Any]],
] = {
    "paths_exist": _paths_exist,
    "multi_asset_research_evidence": _multi_asset_research_evidence,
    "text_contains_all": _text_contains_all,
    "manual_evidence": _manual_evidence,
    "alpha_trial_ledger_evidence": _alpha_trial_ledger_evidence,
    "l2_backup_evidence": _l2_backup_evidence,
    "l2_restore_history_evidence": _l2_restore_history_evidence,
    "risk_recovery_evidence": _risk_recovery_evidence,
    "supply_chain_baseline_evidence": _supply_chain_baseline_evidence,
    "model_risk_baseline_evidence": _model_risk_baseline_evidence,
    "strong_identity_baseline_evidence": _strong_identity_baseline_evidence,
}


def load_requirements(path: str | Path) -> list[dict[str, Any]]:
    registry_path = Path(path)
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot load registry: {exc}") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("requirements"), list):
        raise RegistryError("registry requires schema_version=1 and a requirements list")
    requirements = payload["requirements"]
    seen: set[str] = set()
    for index, item in enumerate(requirements):
        _validate_requirement(item, index)
        requirement_id = item["id"]
        if requirement_id in seen:
            raise RegistryError(f"duplicate requirement id: {requirement_id}")
        seen.add(requirement_id)
    return sorted(requirements, key=lambda item: item["id"])


def _validate_requirement(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise RegistryError(f"requirement {index} must be an object")
    required = {
        "id",
        "domain",
        "tier",
        "target_maturity",
        "classification",
        "description",
        "required_evidence",
        "red_line",
        "checker",
        "next_action",
    }
    missing = sorted(required - item.keys())
    if missing:
        raise RegistryError(f"requirement {index} missing fields: {', '.join(missing)}")
    if not isinstance(item["id"], str) or not item["id"]:
        raise RegistryError("id must be a non-empty string")
    _require_enum(item, "domain", DOMAINS)
    _require_enum(item, "tier", TIERS)
    _require_enum(item, "target_maturity", MATURITIES)
    _require_enum(item, "classification", CLASSIFICATIONS)
    _require_enum(item, "checker", set(CHECKERS))
    evidence = item["required_evidence"]
    if not isinstance(evidence, list) or not evidence or any(value not in EVIDENCE_TYPES for value in evidence):
        raise RegistryError("required_evidence contains an invalid value")
    if not isinstance(item["red_line"], bool):
        raise RegistryError("red_line must be boolean")
    if "checker_args" in item and not isinstance(item["checker_args"], dict):
        raise RegistryError("checker_args must be an object")
    if "assess_local_readiness" in item:
        if not isinstance(item["assess_local_readiness"], bool):
            raise RegistryError("assess_local_readiness must be boolean")
        if (
            item["assess_local_readiness"]
            and item["classification"] not in {"NEEDS_EXTERNAL", "POST_L2"}
        ):
            raise RegistryError(
                "assess_local_readiness is only valid for external or post-L2 requirements"
            )


def _require_enum(item: dict[str, Any], field: str, allowed: set[str]) -> None:
    if item[field] not in allowed:
        raise RegistryError(f"{field} has invalid value: {item[field]!r}")


def assess_repository(
    repo_root: str | Path,
    requirements_path: str | Path,
    *,
    as_of_ms: int | None = None,
) -> dict[str, Any]:
    if as_of_ms is None:
        as_of_ms = time.time_ns() // 1_000_000
    if type(as_of_ms) is not int or as_of_ms < 0:
        raise ValueError("as_of_ms must be a non-negative integer")
    context = AssessmentContext(as_of_ms=as_of_ms)
    root = Path(repo_root).resolve()
    requirements = load_requirements(requirements_path)
    assessed: list[dict[str, Any]] = []
    for requirement in requirements:
        externally_gated = requirement["classification"] in {"NEEDS_EXTERNAL", "POST_L2"}
        assess_local_readiness = bool(requirement.get("assess_local_readiness", False))
        if externally_gated and not assess_local_readiness:
            result = _manual_evidence(
                root,
                {},
                requirement["target_maturity"],
                context,
            )
        else:
            checker = CHECKERS[requirement["checker"]]
            result = checker(
                root,
                requirement.get("checker_args", {}),
                requirement["target_maturity"],
                context,
            )
            if externally_gated and result["status"] == "PASS":
                # Local readiness is useful evidence, but it can never satisfy a
                # requirement whose actual target is external or post-L2.
                result = {
                    **result,
                    "status": "PARTIAL",
                    "detail": (
                        result["detail"]
                        + "; local readiness only; external target evidence remains required"
                    ),
                }
        if result["status"] not in STATUSES or result["current_maturity"] not in MATURITIES:
            raise RegistryError(f"checker returned invalid result for {requirement['id']}")
        meets_target = result["status"] == "PASS" and (
            _maturity_value(result["current_maturity"]) >= _maturity_value(requirement["target_maturity"])
        )
        if result["status"] == "PASS" and not meets_target:
            result = {
                **result,
                "status": "PARTIAL",
                "detail": result["detail"] + "; evidence does not meet target maturity",
            }
        assessed.append(
            {
                **requirement,
                **result,
                "meets_target": meets_target,
            }
        )

    red_lines_failed = sum(1 for item in assessed if item["red_line"] and item["status"] != "PASS")
    tier_a = [item for item in assessed if item["tier"] == "A"]
    if red_lines_failed:
        verdict = "NOT_TOP_TIER"
    elif tier_a and all(item["meets_target"] for item in tier_a):
        verdict = "TOP_TIER_RESEARCH"
    else:
        verdict = "IN_PROGRESS"

    domains = []
    for domain in sorted({item["domain"] for item in assessed}):
        items = [item for item in assessed if item["domain"] == domain]
        current = min(items, key=lambda item: _maturity_value(item["current_maturity"]))["current_maturity"]
        domains.append(
            {
                "domain": domain,
                "current_maturity": current,
                "requirements": len(items),
                "passed": sum(1 for item in items if item["status"] == "PASS"),
                "red_lines_failed": sum(1 for item in items if item["red_line"] and item["status"] != "PASS"),
            }
        )
    return {
        "schema_version": 1,
        "verdict": verdict,
        "summary": {
            "total": len(assessed),
            "passed": sum(1 for item in assessed if item["status"] == "PASS"),
            "partial": sum(1 for item in assessed if item["status"] == "PARTIAL"),
            "failed": sum(1 for item in assessed if item["status"] == "FAILED"),
            "unverified": sum(1 for item in assessed if item["status"] == "UNVERIFIED"),
            "manual_evidence_required": sum(1 for item in assessed if item["status"] == "MANUAL_EVIDENCE_REQUIRED"),
            "red_lines_failed": red_lines_failed,
        },
        "domains": domains,
        "requirements": assessed,
    }


def _maturity_value(value: str) -> int:
    return int(value[1:])
