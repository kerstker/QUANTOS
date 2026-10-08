import hashlib
from pathlib import Path
import pytest
from tools.gold_account_resident import run_once, release_digest, render_unit


def test_source_mismatch_stops_before_loading_credentials(tmp_path):
    def forbidden():raise AssertionError('credential access')
    assert run_once(tmp_path,expected_source='0'*64,credentials=forbidden)==78


def test_background_run_never_prompts_and_resumes_state(tmp_path):
    calls=[]
    def monitor(path,**kwargs):
        calls.append(kwargs['resume'])
        (path/'identity.json').touch()
        return {'reason':'DURATION_COMPLETE','status':'STOPPED','history_status':'WINDOW_CHECKED'}
    kw=dict(expected_source=release_digest(),credentials=lambda:('fake','fake'),observe=monitor)
    assert run_once(tmp_path,**kw)==0
    assert run_once(tmp_path,**kw)==0
    assert calls==[False,True]


def test_storage_and_missing_credentials_stop_without_retry(tmp_path):
    def missing():raise ValueError('CREDENTIAL_FILE_UNAVAILABLE')
    assert run_once(tmp_path,expected_source=release_digest(),credentials=missing)==78


def test_network_failure_retries_but_integrity_error_stops(tmp_path):
    kw=dict(expected_source=release_digest(),credentials=lambda:('fake','fake'))
    assert run_once(tmp_path,observe=lambda *a,**k:{'reason':'RECONNECT_EXHAUSTED'},**kw)==75
    assert run_once(tmp_path,observe=lambda *a,**k:{'reason':'EVENT_UNSUPPORTED_OR_INVALID'},**kw)==78


def test_unit_runs_without_tty_with_pinned_source_and_no_secrets(tmp_path):
    unit=render_unit(release=tmp_path/'release',state=tmp_path/'state',python=Path('/usr/bin/python3'),digest='a'*64)
    assert 'Restart=always' in unit and 'RestartPreventExitStatus=78' in unit
    assert 'WantedBy=default.target' in unit and 'UMask=0077' in unit
    assert '--expected-source '+ 'a'*64 in unit
    assert 'ALLOW_ORDER_SUBMISSION=false' in unit and 'LimitCORE=0' in unit
    assert 'EnvironmentFile' not in unit and 'StandardInput=null' in unit


def test_timestamp_expiry_retries_but_permission_rejection_stops(tmp_path):
    kw=dict(expected_source=release_digest(),credentials=lambda:('fake','fake'))
    for code,expected in ((-1021,75),(-2015,78)):
        report={'reason':'PREFLIGHT_INCOMPLETE','preflight_reason':'REQUEST_REJECTED',
                'preflight_request_error':{'endpoint':'/fapi/v3/account','http_status':400,'exchange_code':code}}
        assert run_once(tmp_path,observe=lambda *a,**k:report,**kw)==expected


def test_unreliable_clock_retries_fresh_preflight_without_accepting_failed_run(tmp_path):
    calls=[]
    def observe(path, **kwargs):
        calls.append(kwargs['resume'])
        return {'reason':'PREFLIGHT_INCOMPLETE','preflight_reason':'CLOCK_UNRELIABLE'}
    kw=dict(expected_source=release_digest(),credentials=lambda:('fake','fake'),observe=observe)
    assert run_once(tmp_path,**kw)==75
    assert run_once(tmp_path,**kw)==75
    assert calls==[False,False]
