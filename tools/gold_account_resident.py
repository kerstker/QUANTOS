"""Explicitly authorized read-only background observer; no interactive secrets.

Run by the owner's user service. The agent only installs/observes safe status,
never opens the credential file or receives its values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import signal
import sys

# -I suppresses caller/user-site injection; only this fixed release is added.
ROOT=Path(__file__).resolve().parents[1]
if __package__ in (None,''):sys.path.insert(0,str(ROOT))

from tools.gold_account_monitor import hashes, monitor
from tools.gold_credentials import get_credentials


def release_files():
    return sorted({*hashes(),'tools/gold_account_resident.py','tools/__init__.py',
                   'services/__init__.py','services/research/__init__.py'})


def release_digest():
    values={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in release_files()}
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def no_prompt():raise ValueError('CREDENTIAL_FILE_UNAVAILABLE')


def saved_credentials():return get_credentials(prompt=no_prompt)


def run_once(state,*,expected_source,credentials=None,observe=None):
    # A failure of source identity must precede credential loading or networking.
    if expected_source!=release_digest():
        print(json.dumps({'status':'NEEDS_ATTENTION','reason':'SOURCE_VERSION_MISMATCH'}),flush=True);return 78
    state=Path(state).absolute()
    try:
        if state.resolve()!=state:raise ValueError('LINKED_STATE')
        state.mkdir(mode=0o700,parents=True,exist_ok=True)
        if shutil.disk_usage(state).free<256*1024*1024:raise ValueError('INSUFFICIENT_STORAGE')
        key,secret=(credentials or saved_credentials)()
        try:
            report=(observe or monitor)(state,api_key=key,api_secret=secret,seconds=3600,
                resume=(state/'identity.json').exists(),
                progress=lambda value:print(json.dumps(value,sort_keys=True),flush=True))
        finally:del key,secret
        print(json.dumps(report,sort_keys=True),flush=True)
        reason=report.get('reason')
        if reason in ('DURATION_COMPLETE','OPERATOR_STOPPED'):return 0
        if reason in ('STREAM_FAILED','RECONNECT_EXHAUSTED','HISTORY_FAILED'):return 75
        if reason=='PREFLIGHT_INCOMPLETE':
            error=report.get('preflight_request_error') or {}
            if error.get('exchange_code')==-1021:return 75
            transient={'NETWORK_TIMEOUT','NETWORK_UNAVAILABLE','TLS_FAILED','EXCHANGE_UNAVAILABLE',
                       'REQUEST_FAILED','RATE_LIMITED','CAPTURE_TIMEOUT','CHANGED_DURING_CAPTURE',
                       'CLOCK_UNRELIABLE'}
            if report.get('preflight_reason') in transient:return 75
        return 78
    except KeyboardInterrupt:return 0
    except Exception as exc:
        safe={'CREDENTIAL_FILE_UNAVAILABLE','PRIVATE_CREDENTIAL_DIRECTORY_REQUIRED','PRIVATE_CREDENTIAL_FILE_REQUIRED',
              'INVALID_STORED_CREDENTIALS','ACCOUNT_BINDING_MISMATCH','INSUFFICIENT_STORAGE','JOURNAL_IN_USE'}
        reason=str(exc) if isinstance(exc,ValueError) and str(exc) in safe else 'RESIDENT_VALIDATION_FAILED'
        print(json.dumps({'status':'NEEDS_ATTENTION','reason':reason,'submission_enabled':False}),flush=True)
        return 78


def render_unit(*,release,state,python,digest):
    for p in (release,state,python):
        if not Path(p).is_absolute() or not re.fullmatch('[A-Za-z0-9_./-]+',str(p)):raise ValueError('INVALID_SERVICE_PATH')
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('INVALID_SOURCE_DIGEST')
    return f'''[Unit]
Description=QUANTOS XAUUSDT read-only account monitor
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={release}
ExecStart={python} -I {release}/tools/gold_account_resident.py --run --state-dir {state} --expected-source {digest}
UMask=0077
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIVE_TRADING_ENABLED=false
Environment=REAL_BROKER_ENABLED=false
Environment=ALLOW_ORDER_SUBMISSION=false
Environment=ALLOW_AI_DIRECT_TRADING=false
Environment=REQUIRE_HUMAN_APPROVAL=true
Environment=HTTPS_PROXY=http://127.0.0.1:7890
Environment=HTTP_PROXY=http://127.0.0.1:7890
Environment=https_proxy=http://127.0.0.1:7890
Environment=http_proxy=http://127.0.0.1:7890
Environment=NO_PROXY=localhost,127.0.0.1
StandardInput=null
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=60
RestartPreventExitStatus=78
TimeoutStopSec=45
KillSignal=SIGINT
KillMode=control-group
LimitCORE=0
NoNewPrivileges=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=default.target
'''


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--expected-source')
    args=parser.parse_args(argv)
    if not args.run:
        print(json.dumps({'status':'READY_FOR_USER_SERVICE','credential_access':False,'network_used':False,
                          'source_sha256':release_digest(),'submission_enabled':False}));return 0
    if args.state_dir is None or args.expected_source is None:return 78
    def stop(*args):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    return run_once(args.state_dir,expected_source=args.expected_source)


if __name__=='__main__':raise SystemExit(main())
