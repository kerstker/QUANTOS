"""Offline regressions for malicious HTTP response framing and decoding."""
from http.client import HTTPResponse as StdlibResponse
from io import BytesIO
import zlib

import pytest
from urllib3.exceptions import ProtocolError
from urllib3.response import DeflateDecoder, HTTPResponse


class RecordedInput(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.line_limits = []

    def readline(self, size=-1):
        self.line_limits.append(size)
        return super().readline(size)


class FixtureSocket:
    def __init__(self, data):
        self.fp = RecordedInput(data)

    def makefile(self, *args, **kwargs):
        return self.fp


@pytest.mark.parametrize('stream_method', ['stream', 'read_chunked'])
@pytest.mark.parametrize('frame', ['chunk_size', 'trailer'])
def test_chunk_line_reads_are_bounded_and_overlong_frames_rejected(frame, stream_method):
    long_line = b'x' * 131_072 + b'\r\n'
    body = (b'0;' + long_line if frame == 'chunk_size' else b'0\r\n' + long_line) + b'\r\n'
    sock = FixtureSocket(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' + body)
    origin = StdlibResponse(sock)
    origin.begin()
    sock.fp.line_limits.clear()
    response = HTTPResponse(body=origin, headers={'transfer-encoding': 'chunked'}, preload_content=False)
    try:
        with pytest.raises(ProtocolError, match='maximum allowed length'):
            list(getattr(response, stream_method)(amt=16))
        assert sock.fp.line_limits and all(0 < limit <= 65_537 for limit in sock.fp.line_limits)
    finally:
        response.close()


def test_deflate_decoder_finishes_with_trailing_bytes_after_end_of_stream():
    # A bounded loop reproduces the stuck decoder without hanging a worker.
    original = b'offline-response-fixture-' * 32
    decoder = DeflateDecoder()
    decoded = bytearray(decoder.decompress(zlib.compress(original) + b'trailing', max_length=16))
    for _ in range(len(original) + 1):
        if not decoder.has_unconsumed_tail:
            break
        decoded.extend(decoder.decompress(b'', max_length=16))
    assert bytes(decoded) == original
    assert not decoder.has_unconsumed_tail


def test_https_forwarding_proxy_retains_its_own_tls_identity_policy(monkeypatch):
    import ssl
    from types import SimpleNamespace
    from collections import namedtuple
    from urllib3.connection import HTTPSConnection
    from urllib3._base_connection import ProxyConfig
    from urllib3.util.url import parse_url
    import urllib3.connection as connection_module

    proxy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    target_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    target_context.check_hostname = False
    target_context.verify_mode = ssl.CERT_NONE  # Deliberately different fixture policy; no network.
    settings = []
    fixture_socket = SimpleNamespace(selected_alpn_protocol=lambda: None)
    def wrap(sock=None, **kwargs):
        settings.append(kwargs)
        return namedtuple("VerifiedSocket", "socket is_verified")(fixture_socket, True)
    monkeypatch.setattr(connection_module, '_ssl_wrap_socket_and_match_hostname', wrap)
    monkeypatch.setattr(connection_module.ssl_, 'ALPN_PROTOCOLS', ['http/1.1'])
    connection = HTTPSConnection('proxy.example.invalid', 443,
        proxy=parse_url('https://proxy.example.invalid'),
        proxy_config=ProxyConfig(proxy_context, True, 'proxy.example.invalid', None),
        ssl_context=target_context, cert_reqs=ssl.CERT_NONE,
        server_hostname='target.example.invalid', assert_hostname='target.example.invalid')
    monkeypatch.setattr(connection, '_new_conn', lambda: fixture_socket)
    connection.connect()
    assert len(settings) == 1
    assert settings[0]['ssl_context'] is proxy_context
    assert settings[0]['cert_reqs'] == ssl.CERT_REQUIRED
    assert settings[0]['server_hostname'] == 'proxy.example.invalid'
    assert settings[0]['assert_hostname'] == 'proxy.example.invalid'
    assert proxy_context.verify_mode == ssl.CERT_REQUIRED
    assert connection.proxy_is_verified is True
    assert connection.is_verified is False
