"""Independent receipt bridge for research artifacts, without economic admission."""
from services.governance.artifact_receipt_auth import verify_signed_artifact_receipt


def verify_research_receipt(artifact_hash,envelope,key,*,now):
    base={'economic_eligible':False,'authoritative':False,
          'limitations':['SIGNATURE_DOES_NOT_ESTABLISH_PREDICTIVE_EDGE','EXISTING_ECONOMIC_ADMISSION_UNCHANGED']}
    if envelope is None or key is None:return {**base,'status':'INDEPENDENT_AUTHORITY_UNCONFIGURED'}
    facts=verify_signed_artifact_receipt(envelope,key,required_authority_id=key.authority_id,
        required_key_id=key.key_id,required_public_key_sha256=key.public_key_sha256)
    receipt=envelope.receipt
    if receipt.artifact_sha256!=artifact_hash:raise ValueError('research artifact binding mismatch')
    if type(now) is not int or any(not 0<=now-t<=86400000 for t in (receipt.verified_at_ms,receipt.transparency_checkpoint_at_ms,receipt.revocation_checked_at_ms)):
        raise ValueError('independent receipt stale or future dated')
    if receipt.revocation_status!='GOOD':raise ValueError('independent revocation state not good')
    return {**base,'status':'VERIFIED_SIGNATURE_ENGINEERING_ONLY','authentication':facts.model_dump(mode='json'),
            'verified_at_ms':receipt.verified_at_ms,'receipt_id':receipt.receipt_id}
