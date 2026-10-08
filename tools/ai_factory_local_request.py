"""One bounded loopback inference request; no credentials or proxy discovery."""
import base64
import json
import sys
from tools.ai_factory_provider import _SocketLocalModelTransport,LIMIT


def main():
    try:
        raw=sys.stdin.read(2*LIMIT+1)
        if len(raw)>2*LIMIT:raise ValueError('request too large')
        value=json.loads(raw);body=base64.b64decode(value['body'],validate=True)
        if (set(value)!={'endpoint','body','timeout_seconds','max_response_bytes'} or len(body)>LIMIT
                or type(value['timeout_seconds']) is not int or not 1<=value['timeout_seconds']<=60
                or type(value['max_response_bytes']) is not int or not 1<=value['max_response_bytes']<=LIMIT):
            raise ValueError('bounded local request required')
        response=_SocketLocalModelTransport().post(endpoint=value['endpoint'],headers={},body=body,
            timeout_seconds=value['timeout_seconds'],max_response_bytes=value['max_response_bytes'])
        print(json.dumps({'status':response.status,'body':base64.b64encode(response.body).decode()}))
        return 0
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__}));return 1


if __name__=='__main__':raise SystemExit(main())
