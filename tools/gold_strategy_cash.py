"""Persist the original pre-signal wallet and finish it after strategy closure.

Uses the existing bounded Demo GET collector and its resume protocol. There is
no resident thread to lose on controller restart and no replacement opening.
"""
import time

from live.execution.demo_batch_risk import _digest
from services.research.demo_cash_window import load_capture, save_capture
from tools import binance_demo_cash_window as collector
from tools.cache_io import atomic_write_json
from tools.demo_resident_recovery import _check_path
from services.governance.json_integrity import load_json_without_duplicate_keys


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_STRATEGY_CASH_'+reason)


def _collect(account, **kwargs):
    from live.exchange import demo_network, binance_demo_exact
    opener = demo_network.opener()
    return collector.collect(credential_file=None, operator_read=True, observe_seconds=30,
        credential_reader=lambda _: binance_demo_exact._credentials(account),
        get=lambda path, **kw: collector.get_response(path, opener=opener, **kw), **kwargs)


def _area(root, bundle, account, source_commit):
    require(bundle.get('source_commit') == source_commit, 'SOURCE')
    area = root/'strategy-cash-work'/_digest(bundle)
    for path in (root, area.parent, area):
        if path != root: path.mkdir(mode=0o700, exist_ok=True)
        _check_path(path, root.stat().st_uid, directory=True)
    binding = dict(schema='GOLD_STRATEGY_CASH_BINDING_V1', source_commit=source_commit,
        bundle_sha256=_digest(bundle), account_ref_sha256=account, collector_sha256=collector.source_hash())
    path = area/'binding.json'
    if path.exists() or path.is_symlink():
        _check_path(path, root.stat().st_uid, directory=False)
        require(path.stat().st_size <= 4096 and load_json_without_duplicate_keys(path.read_bytes()) == binding,
                'BINDING_CHANGED')
    else:
        atomic_write_json(path, binding)
    return area


def _prefix(area, account):
    _check_path(area/'opening', area.stat().st_uid, directory=True)
    prefix = load_capture(area/'opening')
    require(prefix['status'] == 'OPENING_CAPTURED' and prefix['account_ref_sha256'] == account
        and prefix['source_sha256'] == collector.source_hash(), 'OPENING_BINDING')
    return prefix


def open_window(root, *, bundle, account, source_commit, observation_exists):
    area = _area(root, bundle, account, source_commit)
    if (area/'opening').exists() or (area/'opening').is_symlink():
        prefix = _prefix(area, account)
    else:
        require(not observation_exists, 'PRE_SIGNAL_OPENING_MISSING')
        prefix = _collect(account, opening_only=True)
        require(prefix['status'] == 'OPENING_CAPTURED' and prefix['account_ref_sha256'] == account,
                'OPENING_UNAVAILABLE')
        save_capture(area/'opening', prefix)
    target = root/'strategy-cash'/_digest(bundle)
    if observation_exists and (target.exists() or target.is_symlink()):
        # This is a retained completed window, not a new live opening. Reuse
        # only after the original closing report and opening binding verify.
        close_window(root, bundle=bundle, account=account, source_commit=source_commit)
        return prefix
    require(0 <= time.time_ns()//1_000_000-prefix['completed_at_ms'] <= 3_000_000, 'OPENING_EXPIRED')
    return prefix


def close_window(root, *, bundle, account, source_commit):
    area = _area(root, bundle, account, source_commit)
    target = root/'strategy-cash'/_digest(bundle)
    prefix = _prefix(area, account)
    if target.exists() or target.is_symlink():
        _check_path(target.parent, root.stat().st_uid, directory=True)
        _check_path(target, root.stat().st_uid, directory=True)
        report = load_capture(target)
        require(report['status'] == 'CAPTURED' and report['account_ref_sha256'] == account
                and report['source_sha256'] == collector.source_hash()
                and report['reads'][:2] == prefix['reads'], 'COMPLETED_BINDING')
        return report
    report = _collect(account, opening_prefix=prefix)
    require(report['account_ref_sha256'] == account, 'ACCOUNT_CHANGED')
    if report['status'] != 'CAPTURED':
        # Keep failure evidence separately so recovery can resume the unchanged
        # opening without overwriting a completed cash window.
        from live.execution.demo_pre_submit import _retain
        failures = area/'failures'; failures.mkdir(mode=0o700, exist_ok=True)
        _retain(failures, report)
        raise ValueError('GOLD_STRATEGY_CASH_CLOSING_INCOMPLETE')
    target.parent.mkdir(mode=0o700, exist_ok=True)
    _check_path(target.parent, root.stat().st_uid, directory=True)
    save_capture(target, report)
    return report
