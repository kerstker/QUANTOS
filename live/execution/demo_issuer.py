"""Issue only the exact grant allowed by installed policy and durable budget.

No broker, human actor, mandate creation, key generation or execution occurs.
The CLI must separately enforce private issuer custody and trusted handoff.
"""
import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution.demo_issuer_policy import review_submission,review_cancellation,current_key,require
from live.execution.demo_issuer_store import unsigned_envelope
from services.validation import demo_submission,demo_cancellation


def _sign(key,body):return key.sign(body)


def _issue(plan,*,policy,registry,key,store,now_ms,clock_ms):
    registered=current_key(policy,registry,now_ms)
    require(isinstance(key,Ed25519PrivateKey) and
            hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()==registered['public_key_sha256'],
            'ISSUER_PRIVATE_KEY_MISMATCH')
    store.reserve(plan,policy=policy,registry=registry)
    envelope=unsigned_envelope(plan,policy)
    protocol=demo_submission if plan['purpose']=='submission' else demo_cancellation
    signature=_sign(key,protocol.signing_payload(envelope))
    envelope['signature_base64url']=base64.urlsafe_b64encode(signature).rstrip(b'=').decode()
    completed_at=now_ms if clock_ms is None else clock_ms()
    scope={'orders':plan['orders']} if plan['purpose']=='submission' else {'commands':plan['commands']}
    protocol.verify(envelope,registry=registry,expected_context=plan['context'],now_ms=completed_at,**scope)
    store.signed(plan['proposal_sha256'],envelope,now_ms=completed_at)
    prefix='DEMO_EXECUTION_SESSION' if plan['purpose']=='submission' else 'DEMO_CANCELLATION_SESSION'
    return {'schema':prefix+'_GRANT_V1','proposal_sha256':plan['proposal_sha256'],'envelope':envelope}


def issue_submission(handoff,*,policy,registry,key,store,now_ms,clock_ms=None):
    plan=review_submission(handoff,policy=policy,registry=registry,now_ms=now_ms)
    return _issue(plan,policy=policy,registry=registry,key=key,store=store,now_ms=now_ms,clock_ms=clock_ms)


def issue_cancellation(handoff,*,policy,registry,key,store,now_ms,clock_ms=None):
    plan=review_cancellation(handoff,policy=policy,registry=registry,history=store.snapshot(),now_ms=now_ms)
    return _issue(plan,policy=policy,registry=registry,key=key,store=store,now_ms=now_ms,clock_ms=clock_ms)
