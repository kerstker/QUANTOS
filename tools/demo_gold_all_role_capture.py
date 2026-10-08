"""Coordinate all original fixed roles and every configured research UID.

This is an operator capture action, not production restoration. Every native
archive must verify before any role closes. Held original-file leases strengthen
input continuity; native restored joins check signed consumption and mirrors.
Neither check certifies a global writer barrier or complete cash reconciliation.
"""
from contextlib import ExitStack
from copy import deepcopy

from tools import demo_gold_file_leases as leases
from tools import demo_gold_research_launcher as research
from tools import demo_gold_role_launcher as fixed
from tools import demo_gold_restored_joins as joins

native = fixed.handoff.native
roster = fixed.handoff.roster
NAMES = ('executor', 'issuer', 'controller')


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_ALL_ROLE_CAPTURE_' + reason)


def capture_all(*, verified_layout, verified_public, source_attachments, file_leases):
    """Keep the entire declared cut open through native verification and exit."""
    captured = roster.require_captured(verified_layout, verified_public=verified_public)
    require(type(file_leases) is leases.OriginalFileReadLeases, 'LIVE_FILE_LEASES_REQUIRED')
    captured = file_leases.guarded_layout(verified_layout=captured, verified_public=verified_public)
    require(type(source_attachments) is dict and set(source_attachments) == {*NAMES, 'research'},
        'COMPLETE_FOUR_ROLE_SCOPE')
    uids = research.peer.ResearchCaptureSet(verified_layout=captured, verified_public=verified_public).uids
    research_sources = source_attachments['research']
    require(type(research_sources) is dict and all(type(uid) is int for uid in research_sources)
        and set(research_sources) == set(uids), 'COMPLETE_RESEARCH_ROSTER')
    snapshot = native._digest(captured.snapshot())
    public_sha = native._digest(captured._public)
    cut_sha = native._digest(captured._cut)
    commit, operation = captured._public['source_commit'], captured._cut['operation_sha256']

    def guard():
        file_leases.recheck()
        roster.require_captured(captured, verified_public=verified_public)
        require(native._digest(captured.snapshot()) == snapshot
            and native._digest(captured._public) == public_sha and native._digest(captured._cut) == cut_sha,
            'ORIGINAL_CUT_CHANGED')

    def bind_request(request, namespace, uid):
        guard()
        request.recheck()
        doc = request.document()
        expected = research.handoff.ResearchRequest if namespace == 'research' else fixed.handoff.RoleRequest
        require(type(request) is expected and request._layout is captured and request._public is verified_public,
            'ORIGINAL_REQUEST_REQUIRED')
        require(doc['uid'] == uid and doc.get('namespace', 'research') == namespace
            and doc['source_commit'] == commit and doc['operation_sha256'] == operation
            and doc['fixed_layout_sha256'] == snapshot and native._digest(doc['public_bundle']) == public_sha
            and native._digest(doc['cut_request']) == cut_sha, 'SINGLE_PUBLIC_CUT_BINDING')

    def bind_peer(controlled, request, namespace, uid):
        bind_request(request, namespace, uid)
        consumer = controlled.peer
        expected = research.peer.ResearchPeer if namespace == 'research' else fixed.peer.RolePeer
        require(type(consumer) is expected and controlled.request is request and consumer._request is request
            and consumer.uid == uid and not consumer._closed, 'ORIGINAL_PEER_REQUIRED')
        consumer._live()
        require(consumer._status is not None and consumer._status['phase'] == 'PREPARED'
            and consumer._status['request_sha256'] == request.request_sha256, 'ORIGINAL_PREPARED_PEER')

    guard()
    with ExitStack() as stack:
        requests, workers = {}, {}
        # Issue every original descriptor before the first worker starts. No
        # subset launcher may close its portion before the final UID verifies.
        for namespace in NAMES:
            uid = native._role_uid(captured._public['registry'], namespace)
            key = (namespace, uid)
            request = stack.enter_context(fixed.handoff.issue(verified_layout=captured,
                verified_public=verified_public, namespace=namespace,
                source_attachment=source_attachments[namespace]))
            bind_request(request, *key)
            requests[key] = request
        for uid in uids:
            key = ('research', uid)
            request = stack.enter_context(research.handoff.issue(verified_layout=captured,
                verified_public=verified_public, uid=uid, source_attachment=research_sources[uid]))
            bind_request(request, *key)
            requests[key] = request
        for key, request in requests.items():
            guard()
            launcher = research if key[0] == 'research' else fixed
            controlled = stack.enter_context(launcher.launch(request))
            controlled.peer.prepare()
            bind_peer(controlled, request, *key)
            workers[key] = controlled
        for key, controlled in workers.items():
            bind_peer(controlled, requests[key], *key)
            controlled.peer.verify_archive()
            require(controlled.peer._verified is not None, 'NATIVE_VERIFICATION_REQUIRED')
        guard()
        joined = stack.enter_context(joins.open_join(peers={name: workers[(name,
            native._role_uid(captured._public['registry'], name))].peer for name in NAMES},
            research_peers={uid: workers[('research', uid)].peer for uid in uids},
            verified_layout=captured, verified_public=verified_public))
        for key, controlled in workers.items():
            bind_request(requests[key], *key)
            controlled.peer.recheck()
        guard()
        joined.recheck()
        captures = {name: {} for name in (*NAMES, 'research')}
        for key, controlled in workers.items():
            bind_request(requests[key], *key)
            status = controlled.peer.close_capture()
            if key[0] == 'research':
                captures['research'][str(key[1])] = status
            else:
                captures[key[0]] = status
        exits = {name: {} for name in (*NAMES, 'research')}
        for key, controlled in workers.items():
            guard()
            receipt = controlled.finish()
            if key[0] == 'research':
                exits['research'][str(key[1])] = receipt
            else:
                exits[key[0]] = receipt
        for controlled in workers.values():
            guard()
            controlled.recheck_exit()
        guard()
        joined.recheck()
        lease_report = file_leases.report()
        result = dict(schema='GOLD_ALL_ROLE_NATIVE_CAPTURE_V1', phase='CLOSED',
            operation_sha256=operation, source_commit=commit, fixed_layout_sha256=snapshot,
            cut_request_sha256=cut_sha, public_bundle_sha256=public_sha,
            roles=[*NAMES, 'research'], research_uids=list(uids), captures=captures, worker_exits=exits,
            native_four_role_archives_verified=True, configured_research_uids_verified=True,
            single_public_cut_bound=True, worker_exit_verified=True,
            original_file_leases_verified=True, original_file_lease_scope_sha256=lease_report['scope_sha256'],
            restored_role_joins=joined.report(),
            cross_role_business_join_verified=False, writer_barrier_verified=False,
            cross_namespace_atomic=False, whole_set_verified=False,
            submission_enabled=False, economic_authority=False)
    guard()
    return deepcopy(result)
