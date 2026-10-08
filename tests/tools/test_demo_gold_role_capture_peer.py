"""Wire status cannot substitute for original fixed-role archive verification."""

from copy import deepcopy
import importlib

import pytest

from tests.tools.test_demo_gold_role_handoff import role_case as role_case, issue
from tests.tools.test_demo_gold_research_handoff import (
    request_case as request_case,
    configured as configured,
    fixed_public as fixed_public,
    book as book,
    env as env,
)


def prepared(request):
    doc = request.document()
    native = importlib.import_module("tools.demo_gold_namespace_checkpoint")
    identity = dict(uid=doc["uid"], gid=doc["uid"], groups=[], pid=12, start_ticks=34)
    receipt = dict(
        schema=native.RECEIPT_SCHEMA,
        operation_sha256=doc["operation_sha256"],
        cut_request_sha256=native._digest(doc["cut_request"]),
        namespace=doc["namespace"],
        source_commit=doc["source_commit"],
        registry_sha256=native._digest(doc["public_bundle"]["registry"]),
        inventory_sha256="a" * 64,
        semantic_view_sha256="b" * 64,
        source_attachment_sha256=doc["source_attachment_sha256"],
        before_sha256="c" * 64,
        after_sha256="c" * 64,
        started_at_ms=doc["cut_request"]["opening_at_ms"],
        completed_at_ms=doc["cut_request"]["opening_at_ms"] + 1,
        stable=True,
        worker_identity=identity,
        submission_enabled=False,
        economic_authority=False,
    )
    value = dict(
        schema="GOLD_FIXED_ROLE_CAPTURE_STATUS_V1",
        phase="PREPARED",
        request_sha256=request.request_sha256,
        namespace=doc["namespace"],
        uid=doc["uid"],
        archive="archive",
        archive_manifest_sha256="d" * 64,
        inventory_sha256="a" * 64,
        role_receipt=receipt,
        writer_barrier_verified=False,
        whole_set_verified=False,
        submission_enabled=False,
        economic_authority=False,
    )
    return value, identity


@pytest.mark.parametrize("namespace", ["executor", "issuer", "controller"])
def test_role_status_checks_bound_identity_and_control_order(role_case, namespace):
    from tools.demo_gold_role_capture_peer import _status

    with issue(role_case, namespace) as request:
        value, identity = prepared(request)
        assert _status(value, request=request, identity=identity, expected_phase="PREPARED") == value
        updated = deepcopy(value)
        updated["phase"] = "RECHECKED"
        updated["role_receipt"]["completed_at_ms"] += 1
        assert (
            _status(updated, request=request, identity=identity, expected_phase="RECHECKED", previous=value) == updated
        )
        with pytest.raises(ValueError, match="STATUS_ORDER"):
            _status(
                dict(updated, phase="CLOSED"),
                request=request,
                identity=identity,
                expected_phase="CLOSED",
                previous=value,
            )


@pytest.mark.parametrize(
    "damage",
    [
        "research",
        "schema",
        "uid",
        "bool_uid",
        "archive",
        "writer",
        "whole",
        "economic",
        "submission",
        "inventory",
        "receipt_role",
        "worker",
        "groups",
        "time",
        "changed",
    ],
)
def test_status_claims_cannot_change_capture_scope(role_case, damage):
    from tools.demo_gold_role_capture_peer import _status

    with issue(role_case, "controller") as request:
        value, identity = prepared(request)
        identity = deepcopy(identity)
        if damage == "research":
            value["namespace"] = "research"
        elif damage == "schema":
            value["schema"] = "GOLD_RESEARCH_CAPTURE_STATUS_V1"
        elif damage == "uid":
            value["uid"] = 1
        elif damage == "bool_uid":
            value["uid"] = False
        elif damage == "archive":
            value["archive"] = "../archive"
        elif damage in ("writer", "whole", "economic", "submission"):
            value[
                {
                    "writer": "writer_barrier_verified",
                    "whole": "whole_set_verified",
                    "economic": "economic_authority",
                    "submission": "submission_enabled",
                }[damage]
            ] = True
        elif damage == "inventory":
            value["inventory_sha256"] = "e" * 64
        elif damage == "receipt_role":
            value["role_receipt"]["namespace"] = "executor"
        elif damage == "worker":
            value["role_receipt"]["worker_identity"]["pid"] += 1
        elif damage == "groups":
            value["role_receipt"]["worker_identity"]["groups"] = [1000]
        elif damage == "time":
            value["role_receipt"]["completed_at_ms"] = 0
        elif damage == "changed":
            value["role_receipt"]["after_sha256"] = "e" * 64
        with pytest.raises(ValueError):
            _status(value, request=request, identity=identity, expected_phase="PREPARED")


def test_role_worker_rejects_research_lease_before_native_access():
    from tools import demo_gold_role_capture_worker as worker
    from tools.demo_gold_research_handoff import ResearchLease

    with pytest.raises(ValueError, match="ADMITTED_ROLE_LEASE_REQUIRED"):
        worker._capture(object.__new__(ResearchLease), None)
