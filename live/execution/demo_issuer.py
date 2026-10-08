"""Issue only the exact grant allowed by installed policy and durable budget.

No broker, human actor, mandate creation, key generation or execution occurs.
The CLI must separately enforce private issuer custody and trusted handoff.
"""
import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution.demo_issuer_policy import review_submission,review_delegated_submission,review_cancellation,current_key,require
from live.execution.demo_issuer_store import unsigned_envelope
from services.validation import demo_submission,demo_cancellation


def _sign(key,body):return key.sign(body)


def _issue(plan,*,policy,registry,key,store,now_ms,clock_ms,delegation=None,before_sign=None,cancellation=None):
    from live.execution.demo_issuer_policy import current_cancellation_key
    registered=(current_cancellation_key if plan['purpose']=='cancellation' else current_key)(policy,registry,now_ms)
    require(isinstance(key,Ed25519PrivateKey) and
            hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()==registered['public_key_sha256'],
            'ISSUER_PRIVATE_KEY_MISMATCH')
    store.reserve(plan,policy=policy,registry=registry,**({'delegation':delegation} if delegation is not None else {}),
        **({'cancellation':cancellation} if cancellation is not None else {}))
    signing_floor=now_ms
    if before_sign is not None:
        checked_at=before_sign()
        if checked_at is not None:
            require(type(checked_at) is int and checked_at>=now_ms,'ISSUER_SIGNING_CLOCK_REGRESSION')
            signing_floor=checked_at
    envelope=unsigned_envelope(plan,policy)
    protocol=demo_submission if plan['purpose']=='submission' else demo_cancellation
    from live.execution.demo_gold_exact_scope import SCHEMA, verify_history
    if policy.get('schema')==SCHEMA:
        verify_history(policy,store.snapshot())
    signature=_sign(key,protocol.signing_payload(envelope))
    envelope['signature_base64url']=base64.urlsafe_b64encode(signature).rstrip(b'=').decode()
    completed_at=now_ms if clock_ms is None else clock_ms()
    require(type(completed_at) is int and completed_at>=signing_floor,'ISSUER_SIGNING_CLOCK_REGRESSION')
    scope={'orders':plan['orders']} if plan['purpose']=='submission' else {'commands':plan['commands']}
    protocol.verify(envelope,registry=registry,expected_context=plan['context'],now_ms=completed_at,**scope)
    store.signed(plan['proposal_sha256'],envelope,now_ms=completed_at)
    prefix='DEMO_EXECUTION_SESSION' if plan['purpose']=='submission' else 'DEMO_CANCELLATION_SESSION'
    return {'schema':prefix+'_GRANT_V1','proposal_sha256':plan['proposal_sha256'],'envelope':envelope}


def issue_submission(handoff,*,policy,registry,key,store,now_ms,clock_ms=None):
    plan=review_submission(handoff,policy=policy,registry=registry,now_ms=now_ms)
    return _issue(plan,policy=policy,registry=registry,key=key,store=store,now_ms=now_ms,clock_ms=clock_ms)


def issue_delegated_submission(
    handoff, *, policy, registry, public_material, key, store, now_ms, clock_ms=None, refresh=None
):
    """Reserve on the original ledger, recheck current trust, then sign once.

    Runtime refresh rereads issuer-owned configuration and public ENABLE bytes.
    Pure callers still revalidate their supplied trust material at signing time.
    Failure after reserve consumes its original budget; no retry or refund.
    """
    checked = review_delegated_submission(
        handoff, policy=policy, registry=registry, public_material=public_material, now_ms=now_ms
    )
    return _issue_delegated(
        checked,
        handoff=handoff,
        policy=policy,
        registry=registry,
        public_material=public_material,
        key=key,
        store=store,
        now_ms=now_ms,
        clock_ms=clock_ms,
        refresh=refresh,
    )


def _issue_delegated(checked, *, handoff, policy, registry, public_material, key, store, now_ms, clock_ms, refresh):
    # Private orchestration after review permits the CLI to reject bad input
    # before opening its key, without replaying the initial proof twice.
    def before_sign():
        latest = refresh() if refresh is not None else (policy, registry, public_material)
        require(latest == (policy, registry, public_material), "ISSUER_DELEGATED_CONFIGURATION_CHANGED")
        at = now_ms if clock_ms is None else clock_ms()
        require(type(at) is int and at >= now_ms, "ISSUER_SIGNING_CLOCK_REGRESSION")
        current = review_delegated_submission(
            handoff, policy=latest[0], registry=latest[1], public_material=latest[2], now_ms=at
        )
        completed = at if clock_ms is None else clock_ms()
        require(
            current["effective_policy"] == checked["effective_policy"]
            and current["delegation"] == checked["delegation"]
            and current["plan"]["context"] == checked["plan"]["context"]
            and current["plan"]["orders"] == checked["plan"]["orders"]
            and at <= completed < checked["plan"]["expires_at_ms"],
            "ISSUER_DELEGATED_SIGNING_CHANGED_OR_EXPIRED",
        )
        from live.execution.demo_issuer_policy import current_delegated_key
        current_delegated_key(latest[0], latest[1], completed)
        return completed

    return _issue(
        checked["plan"],
        policy=checked["effective_policy"],
        registry=registry,
        key=key,
        store=store,
        now_ms=now_ms,
        clock_ms=clock_ms,
        delegation=checked["delegation"],
        before_sign=before_sign,
    )


def issue_cancellation(handoff,*,policy,registry,key,store,now_ms,clock_ms=None):
    from live.execution.demo_gold_future_template import SCHEMA
    if policy.get('schema')==SCHEMA:
        from live.execution.demo_issuer_policy import review_template_cancellation
        checked=review_template_cancellation(handoff,template=policy,registry=registry,history=store.snapshot(),now_ms=now_ms)
        return _issue(checked['plan'],policy=checked['policy'],registry=registry,key=key,store=store,now_ms=now_ms,
            clock_ms=clock_ms,cancellation=checked['cancellation'])
    plan=review_cancellation(handoff,policy=policy,registry=registry,history=store.snapshot(),now_ms=now_ms)
    return _issue(plan,policy=policy,registry=registry,key=key,store=store,now_ms=now_ms,clock_ms=clock_ms)
