"""Prevent reintroducing encoded-public-key HMAC confusion (GHSA-r6x4-923q-g947)."""
import base64
import json

import jwt
import pytest


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-32'])
def test_encoded_public_jwk_cannot_be_an_hmac_secret(encoding):
    # Public-shaped fixture only; no signing credentials are involved.
    document = json.dumps({'kty': 'RSA', 'n': base64.urlsafe_b64encode(
        b'\x85' * 256).rstrip(b'=').decode(), 'e': 'AQAB'})
    with pytest.raises(jwt.InvalidKeyError):
        jwt.algorithms.HMACAlgorithm(jwt.algorithms.HMACAlgorithm.SHA256).prepare_key(
            document.encode(encoding))


@pytest.mark.parametrize('path', ['decode', 'jwks_key_lookup'])
def test_nested_payload_is_rejected_with_documented_token_error(path, monkeypatch):
    # Malformed public input only. The key lookup must never reach the network.
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=')
    payload = base64.urlsafe_b64encode(b'[' * 20_000 + b']' * 20_000).rstrip(b'=')
    token = (header + b'.' + payload + b'.Zm9yZ2Vk').decode('ascii')
    if path == 'decode':
        reject = lambda: jwt.decode(token, options={'verify_signature': False})
    else:
        client = jwt.PyJWKClient('https://jwks.example.invalid')
        monkeypatch.setattr(client, 'fetch_data', lambda: pytest.fail('malformed token reached network lookup'))
        reject = lambda: client.get_signing_key_from_jwt(token)
    with pytest.raises(jwt.DecodeError):
        reject()
