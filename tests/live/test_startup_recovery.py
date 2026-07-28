"""Ordered, fail-closed startup recovery choreography (727 §13.5)."""

from live.execution.startup_recovery import run_startup_recovery


def _successful_dependencies(order):
    def step(name, value=True):
        def callback(*args):
            order.append(name)
            return value
        return callback

    return {
        "acquire_leader_lease": step("leader"),
        "sync_clock": step("clock"),
        "load_rules_and_fees": step("rules"),
        "fetch_account": step("account", {"balances": []}),
        "fetch_open_orders": step("open_orders", []),
        "fetch_recent_trades": step("trades", []),
        "rebuild_local_ledger": lambda account, orders, trades: (
            order.append("ledger") or {"BTC": 0.01}
        ),
        "verify_event_chain": step("event_chain"),
        "establish_user_stream": step("stream"),
        "data_health": step("data_health"),
        "account_health": step("account_health"),
    }


def test_success_runs_in_documented_order_and_allows_new_orders():
    order = []
    dependencies = _successful_dependencies(order)
    report = run_startup_recovery(
        **dependencies,
        ledger_positions={"BTC": 0.01},
        venue_positions={"BTC": 0.01},
    )
    assert report.ready_for_new_orders is True
    assert report.status == "READY"
    assert report.blockers == ()
    assert order == [
        "leader", "clock", "rules", "account", "open_orders", "trades",
        "ledger", "event_chain", "stream", "data_health", "account_health",
    ]
    assert [step.name for step in report.steps] == [
        "leader_lease", "time_sync", "rules_and_fees", "balances", "open_orders",
        "recent_trades", "rebuild_local_ledger", "event_chain", "reconciliation",
        "user_stream", "data_health", "account_health",
    ]


def test_mexc_without_user_stream_is_not_ready_and_does_not_claim_completion():
    order = []
    dependencies = _successful_dependencies(order)
    dependencies.pop("establish_user_stream")
    report = run_startup_recovery(
        **dependencies,
        ledger_positions={"BTC": 0.01},
        venue_positions={"BTC": 0.01},
    )
    assert report.ready_for_new_orders is False
    assert "USER_STREAM_NOT_READY" in report.blockers
    assert "data_health" not in order


def test_reconciliation_drift_hard_stops_before_user_stream():
    order = []
    dependencies = _successful_dependencies(order)
    report = run_startup_recovery(
        **dependencies,
        ledger_positions={"BTC": 0.01},
        venue_positions={"BTC": 0.02},
    )
    assert report.ready_for_new_orders is False
    assert "RECONCILIATION_REQUIRED" in report.blockers
    assert "stream" not in order


def test_missing_leader_lease_stops_before_any_venue_read():
    order = []
    dependencies = _successful_dependencies(order)
    dependencies["acquire_leader_lease"] = None
    report = run_startup_recovery(
        **dependencies,
        ledger_positions={"BTC": 0.01},
        venue_positions={"BTC": 0.01},
    )
    assert report.ready_for_new_orders is False
    assert report.blockers == ("LEADER_LEASE_NOT_ACQUIRED",)
    assert order == []
