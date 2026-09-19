"""Human-started read-only portfolio observation; never an order executor."""
import fcntl
import os
import time
from pathlib import Path

from services.data.kraken_public import require
from services.portfolio.kraken_allocation import validate_policy
from tools import kraken_account_snapshot as account
from tools import kraken_portfolio_cycle as cycle
from tools import kraken_portfolio_observer as observer
from tools import kraken_public_capture as capture


def prepare(root, config, *, policy_path=None, valuation_quote=None):
    observer.validate_config(config)
    require(config['poll_seconds'] <= 3600, 'KRAKEN_ACCOUNT_POLL_INTERVAL')
    root = account.private_root(root)
    policy_path = Path(policy_path).absolute() if policy_path is not None else None
    policy = validate_policy(cycle.read(policy_path)) if policy_path is not None else None
    binding = {'market': config, 'policy_path': str(policy_path) if policy_path else None,
               'policy_hash': capture.digest(policy) if policy is not None else None,
               'valuation_quote': valuation_quote}
    cycle.bind(root / 'portfolio-session-config.json', binding)
    portfolio = cycle.private_directory(root / 'portfolio')
    cycle.private_directory(portfolio / 'market')
    require(not (root / 'portfolio-session-status.json').is_symlink(), 'KRAKEN_SESSION_SYMLINK')
    return root, portfolio, policy_path


def run_session(client, root, config, *, cycles=1, policy_path=None, valuation_quote=None,
                observe=None, collect_fn=None, clock_ms=None, sleep_fn=None, on_report=None,
                client_order_requests=None):
    root = account.private_root(root)
    fd = os.open(root / '.portfolio-session.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root, portfolio, policy_path = prepare(root, config, policy_path=policy_path,
                                             valuation_quote=valuation_quote)
        clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        observe = observe or observer.run_once
        collect_fn = collect_fn or account.collect
        market = None
        last = None

        def publish(status, **fields):
            record = {'schema': 'KRAKEN_PORTFOLIO_SESSION_V1', 'status': status,
                      'as_of_ms': clock_ms(), 'account_ref_sha256': client.account_ref_sha256,
                      'submission_enabled': False, 'authoritative': False, **fields}
            record['result_hash'] = capture.digest(record)
            capture.atomic_json(root / 'portfolio-session-status.json', record)

        def collect(reader):
            nonlocal market
            publish('REFRESH_IN_PROGRESS')
            market = cycle.inventory.sealed(observe(portfolio / 'market', config))
            require(market['status'] == 'CAPTURED' and
                    cycle.read(portfolio / 'market' / 'status.json') == market,
                    'KRAKEN_SESSION_MARKET_UNRESOLVED')
            return collect_fn(reader)

        def reviewed(event):
            nonlocal last
            # Reuse this cycle's public observation, not a second network sample.
            last = cycle.run_once(portfolio, config, account_path=root / 'latest.json',
                policy_path=policy_path, valuation_quote=valuation_quote,
                observe=lambda *_: market, clock_ms=clock_ms)
            publish('OBSERVATION_COMPLETE', portfolio_status=last['status'],
                    portfolio_hash=last['result_hash'], account_hash=event['result_hash'])
            if on_report is not None:
                on_report({**event, 'portfolio_status': last['status'], 'portfolio_hash': last['result_hash']})

        try:
            result = account.run_session(client, root, cycles=cycles,
                poll_seconds=config['poll_seconds'], collect_fn=collect, sleep_fn=sleep_fn,
                clock_ms=clock_ms, on_report=reviewed, auto_cash=True,
                settlement_market_root=portfolio / 'market', fee_market_root=portfolio / 'market',
                client_order_requests=client_order_requests)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            publish('FAILED', error_type=type(exc).__name__)
            raise
        return {**result, 'portfolio_status': last['status'] if last else 'NOT_OBSERVED',
                'portfolio_hash': last['result_hash'] if last else None}
