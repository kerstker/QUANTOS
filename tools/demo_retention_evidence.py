"""Export reverified isolated rehearsal inputs; never issue execution authority."""

import hashlib

from tools import demo_maintenance_rehearsal as rehearsal
from tools import demo_namespace_checkpoint as checkpoint
from tools import demo_retention_transaction as retention


def export_verified(workspace, destination):
    workspace = retention.isolated(workspace)
    destination = retention.isolated(destination)
    retention.directory(destination.parent)
    with rehearsal.completed_evidence(workspace) as (request, manifest, _):
        destination.mkdir(mode=0o700)
        retention.sync(destination.parent)
        checkpoint._copy_private_capture(
            workspace / "capture.zip", destination / "capture.zip", request["capture_sha256"]
        )
        inputs = {"capture.zip": request["capture_sha256"]}
        for name in ("plan", "review"):
            raw = retention.read(workspace / (name + ".json"))
            digest = hashlib.sha256(raw).hexdigest()
            retention.require(digest == request[name + "_sha256"], "REQUEST_INPUT_CHANGED")
            retention.write_new(destination / (name + ".json"), raw)
            inputs[name + ".json"] = digest
        raw = checkpoint.canonical(manifest)
        retention.write_new(destination / "backup-manifest.json", raw)
        inputs["backup-manifest.json"] = hashlib.sha256(raw).hexdigest()
        proof = {
            "schema": "DEMO_RETENTION_BACKUP_VERIFICATION_V1",
            "status": "REHEARSAL_COMPLETE",
            "capture_sha256": inputs["capture.zip"],
            "manifest_sha256": inputs["backup-manifest.json"],
            "plan_sha256": inputs["plan.json"],
            "review_sha256": inputs["review.json"],
        }
    # Completion is published only after the context's post-copy verification.
    raw = checkpoint.canonical(proof)
    retention.write_new(destination / "verification.json", raw)
    inputs["verification.json"] = hashlib.sha256(raw).hexdigest()
    return {
        "status": "VERIFIED_INPUTS_EXPORTED",
        "inputs": inputs,
        "authorization_issued": False,
        "production_changes": False,
        "all_references_proven": False,
    }
