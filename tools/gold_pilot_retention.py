"""Native observation retention across executor and controller custody.

Original canonical bytes and hashes are preserved. Sampling capacity belongs
only to the original enrollment and never grants execution authority.
"""

from contextlib import ExitStack
from copy import deepcopy
import os
import hashlib
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools.demo_request_prepare import destination
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_pilot_capacity import original_context, _read
from tools.gold_pilot_retention_store import Retention, AREA, MARKER, AUDIT, active, canonical

BAR_SCHEMAS = (
    "GOLD_BAR_RETAINED_EPISODE_INPUT_V1",
    "GOLD_BAR_FRESH_EPISODE_INPUT_V1",
    "GOLD_BAR_AUTO_EPISODE_INPUT_V1",
)


def _base(root, bundle, uid):
    path = Path(root) / MARKER
    if not path.exists() and not path.is_symlink():
        require(not active(root), "RETENTION_HISTORY_MISSING")
        return dict(bundle)
    saved = decode(_read(path, uid))["binding"]["base_runtime"]
    omit = ("episode_id",) if bundle["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1" else ()
    require(
        {k: v for k, v in saved.items() if k not in omit} == {k: v for k, v in bundle.items() if k not in omit},
        "RETENTION_RUNTIME_CHANGED",
    )
    return saved


def _original(root, bundle, *, uid, account, allow_pending=False):
    value = original_context(root, bundle, uid=uid, account=account, allow_pending=allow_pending)
    require(not active(root) or value is not None and value["configuration"] is not None, "RETENTION_ORIGINAL_MISSING")
    return value


def executor_retention(root, bundle, *, source_commit, account, uid):
    if bundle.get("schema") not in BAR_SCHEMAS:
        require(not active(root), "RETENTION_RUNTIME_CHANGED")
        return None
    require(bundle["source_commit"] == source_commit, "RETENTION_SOURCE")
    base = _base(root, bundle, uid)
    original = _original(root, base, uid=uid, account=account)
    if original is None:
        return None
    return Retention(root, original=original, base_runtime=base, uid=uid, role="executor")


def claim_observation(store, bundle, *, clock, sleep):
    """Wait at most one sampling interval, before new observation."""
    now = clock()
    row = store.conn.execute("SELECT claim FROM frames ORDER BY ordinal DESC LIMIT 1").fetchone()
    if row:
        elapsed = now - decode(row[0])["admitted_at_ms"]
        require(elapsed >= 0, "RETENTION_CLOCK_ROLLBACK")
        interval = store.binding["minimum_interval_ms"]
        if elapsed < interval:
            sleep((interval - elapsed) / 1000)
            now = clock()
    return store.claim(bundle=bundle, now_ms=now)


class ControllerRetention:
    """Cold history review precedes worker launch and fresh signal capture."""

    def __init__(self, root, *, base_runtime, source_commit, account, executor_uid):
        self.root = Path(root)
        self.base = dict(base_runtime)
        self.source = source_commit
        self.account = account
        self.executor_uid = executor_uid
        self.store = None
        self._feedback_enabled = False
        self._feedback_source = None
        self._feedback_claims = {}
        self._feedback_record_cursor = 0
        self._feedback_classified = {}
        self._feedback_owner = self._feedback_identity()
        self.enabled = self.base.get("schema") in BAR_SCHEMAS
        if not self.enabled:
            require(not active(self.root) and not active(installer.EXECUTOR), "RETENTION_RUNTIME_CHANGED")
            return
        require(self.base["source_commit"] == source_commit, "RETENTION_SOURCE")
        try:
            original = _original(installer.EXECUTOR, self.base, uid=executor_uid, account=account, allow_pending=True)
            self.enabled = original is not None
            require(not active(self.root) or self.enabled, "RETENTION_ORIGINAL_MISSING")
            if original is not None and original["configuration"] is not None:
                if active(installer.EXECUTOR):
                    from tools.gold_pilot_reobservation import present_reobservation
                    with self._executor(original, verify_history=present_reobservation(installer.EXECUTOR)) as source:
                        chain = self._reobservation(source)
                        self._open(original)
                        self._copy_missing(source, chain=chain)
                else:
                    require(not active(self.root), "RETENTION_EXECUTOR_HISTORY_MISSING")
                    # No history exists to cold-audit yet. Defer the first
                    # controller marker until a native executor result exists.
        except BaseException:
            self.close()
            raise

    def _executor(self, original, *, verify_history=False):
        return Retention(
            installer.EXECUTOR,
            original=original,
            base_runtime=self.base,
            uid=self.executor_uid,
            role="executor",
            read_only=True,
            verify_history=verify_history,
        )

    def _open(self, original):
        if self.store is None:
            self.store = Retention(
                self.root, original=original, base_runtime=self.base, uid=os.geteuid(), role="controller"
            )
        else:
            if self.store.lock is None:
                self.store.acquire()
            require(
                self.store.binding["configuration_sha256"] == _digest(original["configuration"])
                and self.store.binding["request_sha256"] == _digest(original["request"]),
                "RETENTION_ORIGINAL_CHANGED",
            )

    def _reobservation(self, source, *, current_runtime=None):
        from tools.gold_pilot_reobservation import read_reobservation, DECISION_SCHEMA
        view = read_reobservation(installer.EXECUTOR, retention=source, base_runtime=self.base,
            source_commit=self.source, account=self.account, executor_uid=self.executor_uid,
            expected_runtime=current_runtime)
        old = Path(installer.EXECUTOR) / 'gold-pilot/closure-intent.json'
        if view is None and source is not None and (old.exists() or old.is_symlink()):
            # The immutable diagnostic itself witnesses a lost new intent even
            # when the process stopped before its result/admission was written.
            # Only the two terminal frames can belong to this bounded protocol.
            through = source.metadata()['frames']
            for (identity,) in source.conn.execute(
                    "SELECT identity FROM records WHERE kind='decision' AND ordinal>=?", (max(1, through - 1),)):
                require(decode(source.read('decision', identity)).get('schema') != DECISION_SCHEMA,
                        'RETENTION_REOBSERVATION_INTENT_MISSING')
        return view

    def _copy_missing(self, source, *, chain=None):
        last = self.store.metadata()["frames"]
        # Include the last ordinal in case a controller reserved it but failed
        # before copying its result. This replays no task or approval.
        for ordinal, identity in source.conn.execute(
            "SELECT ordinal,identity FROM records WHERE kind='result' AND ordinal>=? ORDER BY ordinal", (max(1, last),)
        ):
            value = decode(source.read("result", identity))
            claim = decode(source.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()[0])
            require(claim["ordinal"] == ordinal, "RETENTION_AMBIGUOUS_FRAME")
            if chain is not None and claim == chain['failed_claim']:
                from tools.gold_pilot_reobservation import validate_expired_result
                require(value == chain['expired_result'], 'RETENTION_DIAGNOSTIC_CHANGED')
                decision = validate_expired_result(value, intent=chain['intent'])
                require(source.read_claim(claim, 'decision') == canonical(decision),
                        'RETENTION_DIAGNOSTIC_CHANGED')
            else:
                self._validate(value, claim)
            self.store.adopt(claim)
            self.store.put(claim, "result", value)

    def _validate(self, result, claim):
        return validate_retained_result(result, claim, self.store.binding)

    def retain(self, result):
        if not self.enabled:
            _retain(destination(self.root, "strategy-decisions"), result)
            return
        original = _original(installer.EXECUTOR, self.base, uid=self.executor_uid, account=self.account)
        require(original is not None, "RETENTION_ORIGINAL_MISSING")
        # Missing native evidence cannot be replaced by a legacy copy.
        with self._executor(original) as source:
            identity = _digest(result)
            raw = source.read("result", identity)
            require(raw == canonical(result), "RETENTION_EXECUTOR_RESULT_CHANGED")
            claim = source.reference("result", identity)
            self._open(original)
            self._validate(result, claim)
            self.store.adopt(claim)
            self.store.put(claim, "result", result)

    def retain_reobservation_diagnostic(self, result, *, current_runtime):
        """Mirror only the expired original fixed-chain diagnostic, never a grant."""
        from tools.gold_pilot_reobservation import validate_expired_result
        if not self.enabled:
            chain = self._reobservation(None, current_runtime=current_runtime)
            require(chain is not None and chain['expired_result'] == result,
                    'RETENTION_DIAGNOSTIC_CHANGED')
            validate_expired_result(result, intent=chain['intent'])
            _retain(destination(self.root, 'strategy-decisions'), result)
            return
        require(self._feedback_enabled, 'FEEDBACK_DISCOVERY_NOT_ENABLED')
        self._feedback_open()
        chain = self._reobservation(self._feedback_source, current_runtime=current_runtime)
        require(chain is not None and chain['expired_result'] == result, 'RETENTION_DIAGNOSTIC_CHANGED')
        self._copy_missing(self._feedback_source, chain=chain)
        require(self.store.read_claim(chain['failed_claim'], 'result') == canonical(result),
                'RETENTION_DIAGNOSTIC_CHANGED')

    def attach(self, result, kind, value):
        require(kind in ("approval", "delivery"), "RETENTION_ATTACHMENT_KIND")
        if not self.enabled:
            name = "strategy-approval-requests" if kind == "approval" else "strategy-feedback-deliveries"
            _retain(destination(self.root, name), value)
            return
        require(self.store is not None, "RETENTION_RESULT_MISSING")
        if self.store.lock is None:
            self.store.acquire()
        claim = self.store.reference("result", _digest(result))
        if kind == "approval":
            require(value.get("result_sha256") == _digest(result), "RETENTION_APPROVAL_RESULT")
        self.store.put(claim, kind, value)

    def enable_feedback_discovery(self):
        """Opt into full original-history review; never allocate a new frame."""
        require(self.enabled, "FEEDBACK_LEGACY_DISCOVERY_UNSUPPORTED")
        self._feedback_enabled = True
        self._feedback_open()

    def _feedback_identity(self):
        return (str(self.root.absolute()), str(Path(installer.EXECUTOR).absolute()),
                self.source, self.account, self.executor_uid, os.geteuid(), _digest(self.base))

    def _feedback_open(self):
        require(self._feedback_identity() == self._feedback_owner, "FEEDBACK_OWNER_CHANGED")
        require(self._feedback_enabled, "FEEDBACK_DISCOVERY_NOT_ENABLED")
        original = _original(installer.EXECUTOR, self.base, uid=self.executor_uid,
                             account=self.account, allow_pending=True)
        require(original is not None, "RETENTION_ORIGINAL_MISSING")
        if self.store is None:
            if not active(installer.EXECUTOR):
                _initial_feedback(self.root, self.base, original, self.executor_uid)
                return original
            # A first worker may persist its original frame after this owner
            # proved bootstrap. Audit that new history before resuming the same
            # object; mirror only its existing complete results.
            require(original["configuration"] is not None, "RETENTION_ORIGINAL_MISSING")
            self._feedback_source = Retention(installer.EXECUTOR, original=original,
                base_runtime=self.base, uid=self.executor_uid, role="executor",
                read_only=True, verify_history=True)
            self._open(original)
            chain = self._reobservation(self._feedback_source)
            self._copy_missing(self._feedback_source, chain=chain)
        require(original["configuration"] is not None, "RETENTION_ORIGINAL_MISSING")
        self._open(original)
        if self._feedback_source is None:
            self._feedback_source = Retention(installer.EXECUTOR, original=original,
                base_runtime=self.base, uid=self.executor_uid, role="executor",
                read_only=True, verify_history=True)
        elif self._feedback_source.lock is None:
            self._feedback_source.acquire()
        require(self._feedback_source.binding == self.store.binding,
                "FEEDBACK_RETENTION_BINDING_CHANGED")
        chain = self._reobservation(self._feedback_source)
        # A child can finish the original result and exit before its pipe reply.
        # Both stores have now been audited/reacquired. Resume only the existing
        # mirror operation; an ordinary frame without a result still fails below.
        last = self.store.metadata()["frames"]
        for ordinal, identity in self._feedback_source.conn.execute(
                "SELECT ordinal,identity FROM records WHERE kind='result' AND ordinal>=? ORDER BY ordinal",
                (max(1, last),)):
            if self.store.conn.execute("SELECT identity FROM records WHERE ordinal=? AND kind='result'",
                    (ordinal,)).fetchone() != (identity,):
                self._copy_missing(self._feedback_source, chain=chain)
                break
        return original

    def discover_feedback(self, *, current_runtime):
        """Classify the complete current/previous scope, using no new claim.

        The cached summaries hold authenticated decisions, never whole results.
        Full cold history checks and append refresh precede this selection.
        """
        return self._discover_feedback(current_runtime=current_runtime, terminal=False)

    def discover_terminal_feedback(self, *, current_runtime):
        """Also resolve the last delivered rotation for terminal observation.

        This read grants no publication, rotation, new observation or execution
        permission. The controller must independently establish terminal control.
        """
        return self._discover_feedback(current_runtime=current_runtime, terminal=True)

    def inspect_reobservation(self, *, current_runtime, now_ms):
        """Inspect original evidence under both native leases, without allocation."""
        from tools import gold_pilot_closure as closure, gold_pilot_reobservation as retry
        require(type(now_ms) is int and 0 < now_ms <= 2**63 - 1, 'RETENTION_REOBSERVATION_CLOCK')
        self._feedback_open()
        _feedback_scope(self.root, self.base, self.account, current_runtime)
        if self.store is None:
            return None  # _feedback_open has proved the original bootstrap.
        source = self._feedback_source
        chain = self._reobservation(source, current_runtime=current_runtime)
        candidate = None
        if chain is None:
            old = closure.read_intent(installer.EXECUTOR, retention=source, runtime=current_runtime,
                source_commit=self.source, account=self.account, executor_uid=self.executor_uid)
            if old is None or old['status'] != 'PENDING':
                return None
            # A claim without an account original is not a retry candidate.
            if source.read_claim(old['claim'], 'origin') is None:
                return None
            candidate = retry.inspect_stale_candidate(installer.EXECUTOR, retention=source,
                base_runtime=self.base, runtime=current_runtime, source_commit=self.source,
                account=self.account, executor_uid=self.executor_uid, now_ms=now_ms)
            if candidate is None:
                return None
        return dict(schema='GOLD_PILOT_REOBSERVATION_INSPECTION_V1', candidate=candidate,
            reobservation=chain, controller_checkpoint=self.store.metadata(),
            executor_checkpoint=source.metadata(), submission_enabled=False)

    def preflight_reobservation(self, *, current_runtime, now_ms, request):
        """Check missing controller mirrors against all original finite budgets.

        This is no capacity allocation or executor state authority. Each original
        write still enforces its own quota; the executor independently preflights.
        """
        from tools import gold_pilot_closure as closure, gold_pilot_reobservation as retry
        inspection = self.inspect_reobservation(current_runtime=current_runtime, now_ms=now_ms)
        require(inspection is not None, 'RETENTION_REOBSERVATION_NOT_APPLICABLE')
        chain = inspection['reobservation']
        if chain is None:
            old = closure.read_intent(installer.EXECUTOR, retention=self._feedback_source,
                runtime=current_runtime, source_commit=self.source, account=self.account,
                executor_uid=self.executor_uid)
            original, failed = old['intent']['request'], old['claim']
            evidence = inspection['candidate']
        else:
            original, failed, evidence = chain['request'], chain['failed_claim'], chain
        require(canonical(request) == canonical(retry.request(current_runtime, original, evidence)),
                'RETENTION_REOBSERVATION_REQUEST_CHANGED')
        bounds = None
        sizes = []
        claims = 0
        for ordinal, name in ((failed['ordinal'], 'expired_result'), (failed['ordinal'] + 1, 'result')):
            row = self.store.conn.execute('SELECT claim FROM frames WHERE ordinal=?', (ordinal,)).fetchone()
            if row is None:
                claims += 1
            if self.store.conn.execute("SELECT identity FROM records WHERE ordinal=? AND kind='result'",
                    (ordinal,)).fetchone() is None:
                # Where original bytes already exist, reserve their exact size.
                actual = None if chain is None else chain['expired_result' if name == 'expired_result' else 'result']
                if actual is None and bounds is None:
                    bounds = retry.budget_bounds(original)
                    require(set(bounds) == {'expired_decision', 'expired_result', 'origin', 'decision', 'result'} and
                        all(type(value) is int and value > 0 for value in bounds.values()),
                        'RETENTION_REOBSERVATION_BOUNDS')
                sizes.append(len(canonical(actual)) if actual is not None else bounds[name])
        required = _controller_capacity(self.store, claims=claims, sizes=sizes)
        return dict(schema='GOLD_PILOT_REOBSERVATION_CAPACITY_V1', status='CAPACITY_AVAILABLE',
            source_commit=self.source, account_ref_sha256=self.account, runtime_sha256=_digest(current_runtime),
            request_sha256=_digest(request), controller_checkpoint=self.store.metadata(), required=required,
            checked_at_ms=now_ms, submission_enabled=False, economic_authority=False)

    def _discover_feedback(self, *, current_runtime, terminal):
        original = self._feedback_open()
        scope = _feedback_scope(self.root, self.base, self.account, current_runtime)
        if self.store is None:
            return dict(status="FEEDBACK_NOT_INITIALIZED",
                reason="PILOT_ENROLLMENT_PENDING" if original["configuration"] is None
                else "ORIGINAL_RETENTION_NOT_STARTED", submission_enabled=False)
        source = self._feedback_source
        source_state, controller_state = source.metadata(), self.store.metadata()
        from tools.gold_pilot_closure import read_intent
        chain = self._reobservation(source, current_runtime=scope['current'])
        if chain is None:
            closure = read_intent(installer.EXECUTOR, retention=source, runtime=scope["current"],
                source_commit=self.source, account=self.account, executor_uid=self.executor_uid)
        else:
            # The original request and original settled identity remain fixed;
            # only the independently proved terminal observation is a successor.
            subject = chain['intent']['subject']
            closure = dict(intent=dict(request=chain['request'],
                original_claim=None if chain['request'].get('original_result') is None else
                    source.reference('result', chain['request']['original_result_sha256']),
                original_checkpoint=subject['original_completed_checkpoint']),
                intent_sha256=chain['intent_sha256'], claim=chain['claim'], result=chain['result'],
                status='COMPLETE' if chain['status'] == 'COMPLETE' else 'PENDING')
        pending = closure["claim"] if closure is not None and closure["status"] == "PENDING" else None
        if chain is not None:
            pending = next((claim for claim in (chain['failed_claim'], chain['claim'])
                if claim is not None and source.read_claim(claim, 'result') is None), None)
        if pending is not None:
            # Only the executor's independently verified terminal observation
            # can lack its result. No controller mirror can predate that result.
            require(source_state["frames"] == pending["ordinal"] and
                    controller_state["frames"] == (pending['ordinal'] - 1 if chain is not None else
                        closure["intent"]["original_checkpoint"]["frames"]) and
                    self.store.conn.execute("SELECT claim FROM frames WHERE ordinal=?",
                        (pending["ordinal"],)).fetchone() is None,
                    "FEEDBACK_CLOSURE_MIRROR_CHANGED")
        bundles = {_digest(scope["current"]): "CURRENT"}
        if scope["previous"] is not None:
            bundles[_digest(scope["previous"])] = "PREVIOUS"
        for ordinal, body in source.conn.execute(
                "SELECT ordinal,claim FROM frames WHERE ordinal>? ORDER BY ordinal", (len(self._feedback_claims),)):
            claim = decode(body)
            require(ordinal == len(self._feedback_claims) + 1 == claim["ordinal"],
                    "FEEDBACK_FRAME_SEQUENCE")
            self._feedback_claims[ordinal] = claim["bundle_sha256"]
        require(len(self._feedback_claims) == source_state["frames"], "FEEDBACK_FRAME_SEQUENCE")
        if self._feedback_claims:
            require(self._feedback_claims[source_state["frames"]] in bundles, "FEEDBACK_FRAME_SCOPE")
        indices = {identity: index for index, identity in enumerate(scope["history"])}
        previous_index = -1
        for identity in self._feedback_claims.values():
            index = indices.get(identity)
            require(index is not None and index >= previous_index, "FEEDBACK_FRAME_SCOPE_SEQUENCE")
            previous_index = index
        observed_bundles = set(self._feedback_claims.values())
        require(all(identity in observed_bundles for identity in scope["history"][:-1]),
                "FEEDBACK_ROTATION_CLAIM_MISSING")
        dirty = {ordinal for ordinal, identity in self._feedback_claims.items()
                 if identity in bundles and ordinal not in self._feedback_classified}
        for _, ordinal in source.conn.execute(
                "SELECT sequence,ordinal FROM records WHERE sequence>? ORDER BY sequence",
                (self._feedback_record_cursor,)):
            if self._feedback_claims[ordinal] in bundles:
                dirty.add(ordinal)
        for ordinal in sorted(dirty):
            claim = decode(source.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()[0])
            if pending is not None and claim == pending:
                self._feedback_classified.pop(ordinal, None)
                continue
            raw = source.read_claim(claim, "decision")
            require(raw is not None, "FEEDBACK_INCOMPLETE_FRAME")
            decision = decode(raw)
            diagnostic = chain is not None and claim == chain['failed_claim']
            if diagnostic:
                from tools.gold_pilot_reobservation import validate_expired_result
                require(chain['expired_result'] is not None and decision ==
                    validate_expired_result(chain['expired_result'], intent=chain['intent']),
                    'RETENTION_DIAGNOSTIC_CHANGED')
            else:
                _feedback_decision(decision, claim, source.binding)
            result_row = source.conn.execute(
                "SELECT identity,LENGTH(payload) FROM records WHERE ordinal=? AND kind='result'", (ordinal,)).fetchone()
            require(result_row is not None, "FEEDBACK_INCOMPLETE_FRAME")
            require(self.store.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()
                    == (canonical(claim),), "FEEDBACK_CONTROLLER_MIRROR_MISSING")
            require(self.store.conn.execute(
                "SELECT identity,LENGTH(payload) FROM records WHERE ordinal=? AND kind='result'", (ordinal,)).fetchone()
                    == result_row, "FEEDBACK_CONTROLLER_MIRROR_MISSING")
            self._feedback_classified[ordinal] = (claim["bundle_sha256"], decision["status"], result_row[0],
                diagnostic or "closure_of_result_sha256" in decision or "closure_mode" in decision)
        require(controller_state["frames"] == source_state["frames"] - (pending is not None),
                "FEEDBACK_MIRROR_FRAME_COUNT")
        self._feedback_record_cursor = source_state["records"]
        self._feedback_classified = {ordinal: value for ordinal, value in self._feedback_classified.items()
                                     if value[0] in bundles}
        require(closure is not None or not any(row[3] for row in self._feedback_classified.values()),
                "FEEDBACK_CLOSURE_INTENT_MISSING")
        selected = {}
        for identity, relation in bundles.items():
            candidates = [(ordinal, row[2]) for ordinal, row in self._feedback_classified.items()
                          if row[0] == identity and row[1] == "STRATEGY_EPISODE_SETTLED"]
            require(len(candidates) <= 1, "FEEDBACK_AMBIGUOUS_COMPLETION")
            if candidates:
                ordinal, result_identity = candidates[0]
                claim = decode(source.conn.execute("SELECT claim FROM frames WHERE ordinal=?", (ordinal,)).fetchone()[0])
                selected[relation] = _feedback_view(self.store, source, result_identity, claim,
                    scope=scope, mode="DISCOVERED", absence_verified=True)
        rotated = scope["rotation_result"]
        if rotated is not None and rotated["decision"]["status"] == "STRATEGY_EPISODE_SETTLED":
            previous = selected.get("PREVIOUS")
            require(previous is not None and previous["delivery"] is not None and
                    dict(previous["result"], research_feedback=previous["delivery"]) == rotated,
                    "FEEDBACK_ROTATION_ORIGINAL_MISSING")
        require(source.metadata() == source_state and self.store.metadata() == controller_state,
                "FEEDBACK_CHECKPOINT_CHANGED")
        found = selected.get("CURRENT") or selected.get("PREVIOUS")
        if found is None and terminal and self.base["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1":
            from tools.gold_bar_rotation import feedback_terminal
            historical = feedback_terminal(root=self.root, base=self.base,
                account=self.account, current=current_runtime)
            if historical is not None:
                require(type(historical) is dict and set(historical) == {"result", "rotation_sha256"},
                        "FEEDBACK_TERMINAL_ROTATION")
                result = dict(historical["result"])
                require("research_feedback" in result, "FEEDBACK_TERMINAL_DELIVERY_MISSING")
                result.pop("research_feedback")
                identity = _digest(result)
                rows = source.conn.execute(
                    "SELECT ordinal FROM records WHERE kind='result' AND identity=? LIMIT 2", (identity,)).fetchall()
                require(len(rows) == 1, "FEEDBACK_TERMINAL_RESULT_MISSING_OR_AMBIGUOUS")
                claim = decode(source.conn.execute("SELECT claim FROM frames WHERE ordinal=?", rows[0]).fetchone()[0])
                require(claim["bundle_sha256"] in scope["history"][:-2], "FEEDBACK_TERMINAL_SCOPE")
                found = _feedback_view(self.store, source, identity, claim, scope=scope,
                    mode="TERMINAL_HISTORY", absence_verified=True, terminal_rotation=historical)
        require(source.metadata() == source_state and self.store.metadata() == controller_state,
                "FEEDBACK_CHECKPOINT_CHANGED")
        if closure is not None:
            original = closure["intent"]["request"]
            from tools.gold_pilot_closure import IDLE_SCHEMA
            if original["schema"] == IDLE_SCHEMA:
                require(found is None and closure["intent"]["original_claim"] is None,
                        "FEEDBACK_IDLE_COMPLETION_CONFLICT")
                if closure["status"] == "COMPLETE":
                    require(self.store.read_claim(closure["claim"], "result") == canonical(closure["result"]),
                            "FEEDBACK_IDLE_MIRROR_CHANGED")
                require(source.metadata() == source_state and self.store.metadata() == controller_state,
                        "FEEDBACK_CHECKPOINT_CHANGED")
                return dict(status="IDLE_CLOSURE", mode="IDLE", result=None, delivery=None,
                    closure={key: deepcopy(closure[key]) for key in
                             ("intent_sha256", "status", "claim", "result")},
                    closure_request=deepcopy(original), controller_checkpoint=controller_state,
                    executor_checkpoint=source_state, submission_enabled=False)
            require(found is not None and found["claim"] == closure["intent"]["original_claim"] and
                    found["result_sha256"] == original["original_result_sha256"] and
                    found["result"] == original["original_result"], "FEEDBACK_CLOSURE_ORIGINAL_CHANGED")
            found["closure"] = {key: deepcopy(closure[key]) for key in
                                ("intent_sha256", "status", "claim", "result")}
        return found

    def release(self):
        if self._feedback_source is not None:
            self._feedback_source.release()
        if self.store is not None:
            self.store.release()

    def close(self):
        if self._feedback_source is not None:
            self._feedback_source.close()
            self._feedback_source = None
        if self.store is not None:
            self.store.close()
            self.store = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _feedback_scope(root, base, account, current):
    if base["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1":
        from tools.gold_bar_rotation import feedback_scope, feedback_history
        scope = feedback_scope(root=root, base=base, account=account, current=current)
        history = feedback_history(root=root, base=base, account=account, current=current)
        require(history[-1] == _digest(scope["current"]) and
                (scope["previous"] is None and len(history) == 1 or
                 scope["previous"] is not None and len(history) > 1 and history[-2] == _digest(scope["previous"])),
                "FEEDBACK_ROTATION_SCOPE_CHANGED")
        return dict(scope, history=history)
    require(base["schema"] in BAR_SCHEMAS and current == base, "FEEDBACK_RUNTIME_CHANGED")
    return dict(current=dict(base), previous=None, rotation_result=None, rotation_sha256=None,
                history=(_digest(base),))


def _initial_feedback(root, base, original, executor_uid):
    """Prove only the original pre-retention bootstrap, never missing history."""
    from tools import gold_bar_pilot_capacity as capacity
    require(not active(root) and not active(installer.EXECUTOR), "FEEDBACK_HISTORY_MISSING")
    require(not any(path.exists() or path.is_symlink() for path in
        (Path(installer.EXECUTOR) / "gold-pilot" / name for name in
         ("closure-intent.json", "closure-origin.json", "closure-result.json",
          "closure-reobservation-intent.json", "closure-reobservation-admission.json",
          "closure-reobservation-capture.json", "closure-reobservation-capture-witness.json",
          "closure-expired-result.json",
          "closure-reobservation-origin.json"))), "FEEDBACK_HISTORY_MISSING")
    for location, uid in ((Path(root), os.geteuid()), (Path(installer.EXECUTOR), executor_uid)):
        _check_path(location, uid, directory=True)
        capacity.legacy_empty(location, uid)
        for name in ("strategy-decisions", "strategy-feedback-deliveries", "strategy-approval-requests"):
            path = location / name
            if path.exists() or path.is_symlink():
                _check_path(path, uid, directory=True)
                require(next(path.iterdir(), None) is None, "FEEDBACK_LEGACY_HISTORY_PRESENT")
        if capacity.state_present(location):
            require(base["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1" and
                    original["configuration"] is not None, "FEEDBACK_HISTORY_MISSING")
            area, contract = capacity.storage(location, "rotations", original, base, uid=uid, read_only=True)
            capacity.rotation_history(area, contract, uid)
            require(next(area.iterdir(), None) is None, "FEEDBACK_HISTORY_MISSING")
            for kind in ("history", "observations"):
                require(capacity.usage(area.parent / kind, contract, kind, uid) == [], "FEEDBACK_HISTORY_MISSING")


def _controller_capacity(store, *, claims, sizes):
    """Conservative physical headroom for the fixed missing mirror operations.

    Payload overflow pages use SQLite's 4-byte link overhead. Each insertion
    reserves two splits per level for every affected B-tree (four for a record,
    one for a claim). Binary depth is an upper bound for these nonempty trees.
    No reservation is persisted and no existing frame or quota is reset.
    """
    from tools.gold_pilot_retention_store import (PAGE_BYTES, CLAIM_MAX_BYTES,
        EVENT_MAX_BYTES, EVENT_ALLOCATED_BYTES, ROW_METADATA_BYTES, FIXED_DATABASE_BYTES)
    state, binding = store.metadata(), store.binding
    require(store.role == 'controller' and store.verify_history and store.lock is not None,
            'RETENTION_CAPACITY_AUDIT_REQUIRED')
    require(type(claims) is int and 0 <= claims <= 2 and len(sizes) <= 2 and
            all(type(size) is int and 0 < size <= binding['max_record_bytes'] for size in sizes),
            'RETENTION_REOBSERVATION_BOUNDS')
    records, payload = len(sizes), sum(sizes)
    events = claims + records
    claim_bytes, audit_bytes = claims * CLAIM_MAX_BYTES, events * EVENT_MAX_BYTES
    pages = store.conn.execute('PRAGMA page_count').fetchone()[0]
    database_info = (store.area / 'records.sqlite3').stat()
    database_now = max(database_info.st_size, database_info.st_blocks * 512, pages * PAGE_BYTES)
    directory_now = sum(max(p.stat().st_size, p.stat().st_blocks * 512)
        for p in (store.area, store.audit, store.audit / 'events'))
    # A full leaf can split up each path and split its root. Include totals/head
    # bookkeeping only if there is a missing write; COMPLETE is exactly zero.
    depth = max(1, (binding['max_database_bytes'] // PAGE_BYTES).bit_length())
    overflow = sum(((size + ROW_METADATA_BYTES + PAGE_BYTES - 5) // (PAGE_BYTES - 4)) * PAGE_BYTES
                   for size in sizes)
    database_bytes = (overflow + claim_bytes +
        (records * 4 + claims) * 2 * (depth + 2) * PAGE_BYTES + PAGE_BYTES) if events else 0
    journal_bytes = 2 * (database_now + database_bytes) + FIXED_DATABASE_BYTES if events else 0
    directory_bytes = events * PAGE_BYTES
    required = dict(claims=claims, records=records, audit_events=events, payload_bytes=payload,
        claim_bytes=claim_bytes, audit_bytes=audit_bytes, database_bytes=database_bytes,
        journal_bytes=journal_bytes, audit_allocated_bytes=events * EVENT_ALLOCATED_BYTES,
        directory_bytes=directory_bytes)
    require(state['frames'] + claims <= binding['max_frames'], 'RETENTION_REOBSERVATION_FRAME_BUDGET')
    require(state['records'] + records <= (state['frames'] + claims) *
        binding['role_records_per_frame']['controller'], 'RETENTION_REOBSERVATION_RECORD_BUDGET')
    require(state['bytes'] + payload <= binding['max_total_bytes'], 'RETENTION_REOBSERVATION_BYTE_BUDGET')
    require(state['claim_bytes'] + claim_bytes <= (state['frames'] + claims) * binding['claim_max_bytes'],
            'RETENTION_REOBSERVATION_CLAIM_BUDGET')
    require(state['frames'] + state['records'] + events <= binding['max_audit_events'] and
        state['audit_bytes'] + audit_bytes <= binding['max_audit_bytes'], 'RETENTION_REOBSERVATION_AUDIT_BUDGET')
    require((state['frames'] + state['records'] + events) * EVENT_ALLOCATED_BYTES <=
        binding['max_audit_allocated_bytes'], 'RETENTION_REOBSERVATION_AUDIT_PHYSICAL_BUDGET')
    require(database_now + database_bytes <= binding['max_database_bytes'],
            'RETENTION_REOBSERVATION_DATABASE_BUDGET')
    require(journal_bytes <= binding['max_journal_bytes'], 'RETENTION_REOBSERVATION_JOURNAL_BUDGET')
    require(directory_now + directory_bytes <= binding['max_directory_bytes'],
            'RETENTION_REOBSERVATION_DIRECTORY_BUDGET')
    return required



def validate_retained_result(result, claim, binding):
    """Original exact result and pilot-window checks, without I/O or authority."""
    from tools.gold_strategy_controller import validate_result
    decision = validate_result(result, source_commit=binding['source_commit'],
        bundle_sha256=claim['bundle_sha256'], account=binding['account_ref_sha256'])
    window = result.get('original_pilot_window', {})
    require(all(window.get(key) == binding[key] for key in
        ('request_sha256', 'configuration_sha256', 'started_at_ms', 'ends_at_ms')),
        'RETENTION_RESULT_WINDOW')
    return decision


def verify_retained_pair(source, controller, *, history, closure, reobservation):
    """Audit two already-open original stores without repairing a mirror.

    The caller independently verifies complete rotation history and the fixed
    closure/reobservation chain on the same source lease. Unlike the hot feedback
    selector, an archive must read every retained payload, including old episodes.
    A pending mirror is explicit evidence, never permission to execute or publish.
    """
    for store, role in ((source, 'executor'), (controller, 'controller')):
        require(isinstance(store, Retention) and store.role == role and store.read_only
            and store.verify_history and store.lock is not None, 'RETENTION_PAIR_READ_ONLY_AUDIT')
    require(canonical(source.binding) == canonical(controller.binding), 'RETENTION_PAIR_BINDING')
    require(type(history) in (tuple, list) and history, 'RETENTION_PAIR_HISTORY')
    base = source.binding['base_runtime']
    runtimes = {_digest(runtime): runtime for runtime in history}
    require(len(runtimes) == len(history) and canonical(history[0]) == canonical(base), 'RETENTION_PAIR_HISTORY')
    omitted = {'episode_id'} if base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1' else set()
    require(all(canonical({k:v for k,v in runtime.items() if k not in omitted}) ==
        canonical({k:v for k,v in base.items() if k not in omitted}) for runtime in history), 'RETENTION_PAIR_HISTORY')
    if not omitted:
        require(len(history) == 1, 'RETENTION_PAIR_HISTORY')
    indices = {key: index for index, key in enumerate(runtimes)}
    checkpoints = source.metadata(), controller.metadata()
    first, second = checkpoints
    allowance = 2 if reobservation is not None else 1
    require(0 <= first['frames'] - second['frames'] <= allowance, 'RETENTION_PAIR_MIRROR_PREFIX')
    incomplete = set()
    diagnostic = None
    if reobservation is not None:
        diagnostic = reobservation['failed_claim']
        for claim in (diagnostic, reobservation['claim']):
            if claim is not None and source.read_claim(claim, 'result') is None:
                incomplete.add(claim['ordinal'])
    elif closure is not None and closure['status'] == 'PENDING':
        incomplete.add(closure['claim']['ordinal'])
    require(len(incomplete) <= 1 and (not incomplete or incomplete == {first['frames']}),
        'RETENTION_PAIR_PARTIAL_TAIL')
    digest = hashlib.sha256(); feedback = []; partial = []
    observed = set(); prior = -1; missing = []
    settled = set()
    for ordinal, body in source.conn.execute('SELECT ordinal,claim FROM frames ORDER BY ordinal'):
        claim = decode(body); identity = claim['bundle_sha256']
        index = indices.get(identity)
        require(index is not None and index >= prior, 'FEEDBACK_FRAME_SCOPE_SEQUENCE')
        prior = index; observed.add(identity)
        slots = {kind: source.read_claim(claim, kind) for kind in ('origin', 'decision', 'result')}
        row = controller.conn.execute('SELECT claim FROM frames WHERE ordinal=?', (ordinal,)).fetchone()
        require(row is None or row == (body,), 'RETENTION_PAIR_CLAIM_CHANGED')
        mirrored = None if row is None else controller.read_claim(claim, 'result')
        if ordinal in incomplete:
            require(mirrored is None and row is None, 'RETENTION_PAIR_PARTIAL_MIRROR')
            partial.append(dict(status='VERIFIED_TERMINAL_PARTIAL', ordinal=ordinal,
                proof_sha256=(reobservation or closure)['intent_sha256'],
                retained_kinds=[kind for kind, raw in slots.items() if raw is not None]))
        else:
            require(slots['decision'] is not None and slots['result'] is not None, 'FEEDBACK_INCOMPLETE_FRAME')
            result, decision = decode(slots['result']), decode(slots['decision'])
            if diagnostic is not None and canonical(claim) == canonical(diagnostic):
                from tools.gold_pilot_reobservation import validate_expired_result
                require(canonical(result) == canonical(reobservation['expired_result']) and
                    canonical(validate_expired_result(result, intent=reobservation['intent'])) == slots['decision'],
                    'RETENTION_DIAGNOSTIC_CHANGED')
            else:
                require(canonical(validate_retained_result(result, claim, source.binding)) == slots['decision'],
                    'FEEDBACK_DECISION_CHANGED')
                _feedback_decision(decision, claim, source.binding)
            if slots['origin'] is not None:
                from services.research.demo_execution_origin import validate
                origin = decode(slots['origin']); validate(origin)
                require(origin['status'] == 'CAPTURED' and origin['account_ref_sha256'] == source.binding['account_ref_sha256']
                    and decision['origin_sha256'] == _digest(origin), 'RETENTION_ORIGIN_HASH')
            if mirrored is None:
                missing.append(ordinal)
            else:
                require(mirrored == slots['result'], 'FEEDBACK_EXECUTOR_RESULT_CHANGED')
                require(not missing, 'RETENTION_PAIR_MIRROR_PREFIX')
            if decision['status'] == 'STRATEGY_EPISODE_SETTLED':
                require(identity not in settled, 'FEEDBACK_AMBIGUOUS_COMPLETION'); settled.add(identity)
                if mirrored is not None:
                    view = _feedback_view(controller, source, _digest(result), claim,
                        scope=dict(current=runtimes[identity], previous=None, rotation_sha256=None),
                        mode='EXACT', absence_verified=True)
                    feedback.append(dict(claim_sha256=_digest(claim), result_sha256=_digest(result),
                        bundle_sha256=identity, status=view['status'],
                        delivery_sha256=None if view['delivery'] is None else _digest(view['delivery'])))
        controller_slots = {}
        if row is not None:
            for kind in ('result', 'approval', 'delivery'):
                raw = controller.read_claim(claim, kind)
                controller_slots[kind] = None if raw is None else hashlib.sha256(raw).hexdigest()
                require(kind == 'result' or raw is None or mirrored is not None, 'RETENTION_PAIR_ATTACHMENT_WITHOUT_RESULT')
        digest.update(canonical(dict(claim=claim,
            source={kind: None if raw is None else hashlib.sha256(raw).hexdigest() for kind, raw in slots.items()},
            controller=controller_slots)))
    require(all(identity in observed for identity in tuple(runtimes)[:-1]), 'FEEDBACK_ROTATION_CLAIM_MISSING')
    if missing:
        require(missing == list(range(missing[0], first['frames'] + 1 - bool(incomplete)))
            and len(missing) <= allowance, 'RETENTION_PAIR_MIRROR_PREFIX')
        partial.append(dict(status='CONTROLLER_MIRROR_PENDING', first_ordinal=missing[0], last_ordinal=missing[-1]))
    require((source.metadata(), controller.metadata()) == checkpoints, 'FEEDBACK_CHECKPOINT_CHANGED')
    return dict(schema='GOLD_RETAINED_PAIR_VIEW_V1', executor_checkpoint=first, controller_checkpoint=second,
        frames_sha256=digest.hexdigest(), feedback=feedback, verified_partial=partial,
        mutation_permitted=False)

def _feedback_decision(decision, claim, binding):
    statuses = {"RECOVERY_REQUIRED", "PILOT_CONFIGURATION_REQUIRED", "STRATEGY_EPISODE_SETTLED",
                "HOLDING", "OTHER_POSITION_REQUIRES_RECONCILIATION", "PILOT_STOPPED",
                "NO_ENTRY_SIGNAL", "DECISION_REQUIRES_APPROVAL"}
    require(type(decision) is dict and decision.get("schema") == "GOLD_STRATEGY_CYCLE_DECISION_V1" and
            decision.get("status") in statuses and decision.get("source_commit") == binding["source_commit"] and
            decision.get("account_ref_sha256") == binding["account_ref_sha256"] and
            decision.get("bundle_sha256") == claim["bundle_sha256"] and
            decision.get("submission_enabled") is False and decision.get("economic_authority") is False,
            "FEEDBACK_DECISION_BINDING")


def _feedback_view(controller, source, identity, claim, *, scope, mode, absence_verified,
                   terminal_rotation=None):
    from tools.gold_strategy_controller import validate_result
    raw = controller.read_claim(claim, "result")
    require(raw is not None and _digest(decode(raw)) == identity, "FEEDBACK_RESULT_MISSING")
    require(source.read_claim(claim, "result") == raw, "FEEDBACK_EXECUTOR_RESULT_CHANGED")
    result = decode(raw)
    decision = validate_result(result, source_commit=source.binding["source_commit"],
        bundle_sha256=claim["bundle_sha256"], account=source.binding["account_ref_sha256"])
    require(source.read_claim(claim, "decision") == canonical(decision), "FEEDBACK_DECISION_CHANGED")
    _feedback_decision(decision, claim, source.binding)
    require(all(result.get("original_pilot_window", {}).get(key) == source.binding[key]
                for key in ("request_sha256", "configuration_sha256", "started_at_ms", "ends_at_ms")),
            "RETENTION_RESULT_WINDOW")
    require(decision["status"] == "STRATEGY_EPISODE_SETTLED" and decision.get("position_flat") is True,
            "FEEDBACK_RESULT_NOT_SETTLED")
    if terminal_rotation is None:
        relation = "CURRENT" if claim["bundle_sha256"] == _digest(scope["current"]) else "PREVIOUS"
        require(relation == "CURRENT" or scope["previous"] is not None and
                claim["bundle_sha256"] == _digest(scope["previous"]), "FEEDBACK_FRAME_SCOPE")
    else:
        require(mode == "TERMINAL_HISTORY" and claim["bundle_sha256"] in scope["history"][:-2],
                "FEEDBACK_TERMINAL_SCOPE")
        relation = "HISTORICAL_TERMINAL"
    exported = result.get("feedback")
    retained = type(exported) is dict and exported.get("status") == "BAR_STRATEGY_FEEDBACK_RETAINED"
    if retained:
        require(exported.get("cash_reconciled") is True and
                all(exported.get(key) is False for key in ("submission_enabled", "economic_authority")) and
                all(type(exported.get(key)) is str and re.fullmatch("[a-f0-9]{64}", exported[key])
                    for key in ("feedback_sha256", "artifact_sha256")), "FEEDBACK_EXPORT_BINDING")
    else:
        require(exported is None or type(exported) is dict and
                exported.get("status") == "BAR_CASH_FEEDBACK_REQUIRED" and
                exported.get("submission_enabled") is False, "FEEDBACK_EXPORT_BINDING")
    delivery_bytes = controller.read_claim(claim, "delivery")
    delivery = decode(delivery_bytes) if delivery_bytes is not None else None
    if delivery is not None:
        require(retained and type(delivery) is dict and delivery.get("status") == "RESEARCH_FEEDBACK_DELIVERED" and
                delivery.get("cycle_sha256") == claim["bundle_sha256"] and
                delivery.get("feedback_sha256") == exported["feedback_sha256"] and
                all(delivery.get(key) is False for key in ("source_verified", "submission_enabled", "economic_authority")),
                "FEEDBACK_DELIVERY_BINDING")
    if relation == "PREVIOUS":
        require(delivery is not None and scope["rotation_result"] == dict(result, research_feedback=delivery),
                "FEEDBACK_ROTATION_ORIGINAL_MISSING")
    if terminal_rotation is not None:
        require(delivery is not None and terminal_rotation["result"] == dict(result, research_feedback=delivery)
                and type(terminal_rotation["rotation_sha256"]) is str and
                re.fullmatch("[a-f0-9]{64}", terminal_rotation["rotation_sha256"]),
                "FEEDBACK_TERMINAL_ORIGINAL_MISSING")
    return dict(status="DELIVERED" if delivery is not None else
                ("PENDING_DELIVERY" if retained else "FEEDBACK_NOT_RETAINED"), mode=mode,
        result_sha256=identity, result_bytes=raw, result=result, claim=deepcopy(claim),
        bundle_sha256=claim["bundle_sha256"], relation=relation,
        delivery_state="PRESENT" if delivery is not None else
            ("ABSENCE_VERIFIED" if absence_verified else "ABSENCE_UNVERIFIED"),
        delivery_sha256=_digest(delivery) if delivery is not None else None,
        delivery_bytes=delivery_bytes, delivery=delivery,
        controller_checkpoint=controller.metadata(), executor_checkpoint=source.metadata(),
        rotation_sha256=(scope["rotation_sha256"] if terminal_rotation is None else
                         terminal_rotation["rotation_sha256"]), submission_enabled=False)


def read_feedback_exact(root, result_sha256, *, base_runtime, source_commit, account, executor_uid,
                        expected_claim=None, expected_delivery_sha256=None):
    """Read a known native completion. Fast absence is explicitly unverified."""
    require(base_runtime.get("schema") in BAR_SCHEMAS, "FEEDBACK_LEGACY_DISCOVERY_UNSUPPORTED")
    require(base_runtime.get("source_commit") == source_commit and type(result_sha256) is str and
            re.fullmatch("[a-f0-9]{64}", result_sha256), "FEEDBACK_EXACT_IDENTITY")
    original = _original(installer.EXECUTOR, base_runtime, uid=executor_uid, account=account)
    require(original is not None and original["configuration"] is not None, "RETENTION_ORIGINAL_MISSING")
    with ExitStack() as stack:
        source = stack.enter_context(Retention(installer.EXECUTOR, original=original,
            base_runtime=base_runtime, uid=executor_uid, role="executor", read_only=True, verify_history=False))
        controller = stack.enter_context(Retention(root, original=original, base_runtime=base_runtime,
            uid=os.geteuid(), role="controller", read_only=True, verify_history=False))
        if expected_claim is None:
            rows = controller.conn.execute(
                "SELECT ordinal FROM records WHERE kind='result' AND identity=? LIMIT 2", (result_sha256,)).fetchall()
            require(len(rows) == 1, "FEEDBACK_EXACT_RESULT_MISSING_OR_AMBIGUOUS")
            expected_claim = decode(controller.conn.execute(
                "SELECT claim FROM frames WHERE ordinal=?", rows[0]).fetchone()[0])
        current = base_runtime
        if base_runtime["schema"] == "GOLD_BAR_AUTO_EPISODE_INPUT_V1":
            from tools.gold_bar_rotation import load
            current = load(root=root, base=base_runtime, account=account, read_only=True)
        scope = _feedback_scope(root, base_runtime, account, current)
        value = _feedback_view(controller, source, result_sha256, expected_claim,
            scope=scope, mode="EXACT", absence_verified=False)
        if expected_delivery_sha256 is not None:
            require(type(expected_delivery_sha256) is str and
                    re.fullmatch("[a-f0-9]{64}", expected_delivery_sha256) and
                    value["delivery_sha256"] == expected_delivery_sha256, "FEEDBACK_DELIVERY_MISSING")
        return value


def controller_retain(root, result, *, base_runtime, source_commit, account, executor_uid):
    with ControllerRetention(
        root, base_runtime=base_runtime, source_commit=source_commit, account=account, executor_uid=executor_uid
    ) as session:
        session.retain(result)


def _reader(root, *, source_commit, account):
    root = Path(root)
    if not active(root):
        require(not active(installer.EXECUTOR), "RETENTION_CONTROLLER_HISTORY_MISSING")
        return None
    _check_path(root, os.geteuid(), directory=True)
    metadata = decode(_read(root / MARKER, os.geteuid()))
    base = metadata["binding"]["base_runtime"]
    require(base["source_commit"] == source_commit, "RETENTION_SOURCE")
    from tools.gold_strategy_controller import observation_configuration

    registry, policy = observation_configuration(source_commit)
    require(policy["account_ref_sha256"] == account, "RETENTION_ACCOUNT")
    original = _original(installer.EXECUTOR, base, uid=registry["executor_uid"], account=account)
    require(original is not None, "RETENTION_ORIGINAL_MISSING")
    return Retention(
        root,
        original=original,
        base_runtime=base,
        uid=os.geteuid(),
        role="controller",
        read_only=True,
        verify_history=False,
    )


def _read_record(root, identity, kind, *, source_commit, account):
    require(type(identity) is str and re.fullmatch("[a-f0-9]{64}", identity), "RETENTION_IDENTITY")
    reader = _reader(root, source_commit=source_commit, account=account)
    if reader is None:
        name = "strategy-decisions" if kind == "result" else "strategy-approval-requests"
        return supervisor._read_root_owned_json(Path(root) / name / (identity + ".json"))
    with reader:
        return reader.read(kind, identity)


def read_controller_result(root, identity, *, source_commit, account):
    return _read_record(root, identity, "result", source_commit=source_commit, account=account)


def read_controller_approval(root, identity, *, source_commit, account):
    return _read_record(root, identity, "approval", source_commit=source_commit, account=account)


def controller_result_ids(root, *, source_commit, account, constraints_sha256):
    reader = _reader(root, source_commit=source_commit, account=account)
    found = []
    if reader is None:
        directory = Path(root) / "strategy-decisions"
        supervisor._root_private_directory(directory)
        count = total = 0
        for entry in directory.iterdir():
            count += 1
            require(
                count <= 1000 and re.fullmatch("[a-f0-9]{64}\\.json", entry.name), "STRATEGY_DISPATCH_DECISION_BUDGET"
            )
            raw = supervisor._read_root_owned_json(entry)
            total += len(raw)
            require(total <= 64 * 1024 * 1024, "STRATEGY_DISPATCH_DECISION_BUDGET")
            value = decode(raw)
            require(_digest(value) == entry.stem, "STRATEGY_DISPATCH_DECISION_HASH")
            if _digest(value.get("decision", {}).get("execution_constraints")) == constraints_sha256:
                found.append(entry.stem)
        return found
    with reader:
        for (identity,) in reader.conn.execute("SELECT DISTINCT identity FROM records WHERE kind='result'"):
            value = decode(reader.read("result", identity))
            if _digest(value.get("decision", {}).get("execution_constraints")) == constraints_sha256:
                found.append(identity)
    return found
