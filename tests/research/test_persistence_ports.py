def test_sqlite_adapters_implement_runtime_persistence_ports():
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.paper_trading.forward_store import ForwardLedger
    from services.research.paper_trading.store import PaperTrackStore
    from services.research.ports.persistence import (
        ExperimentStorePort,
        ForwardLedgerPort,
        PaperTrackStorePort,
        RunStorePort,
    )
    from services.research.run_registry.store import ResearchRunStore

    assert isinstance(
        SqliteExperimentStore(":memory:", create=True),
        ExperimentStorePort,
    )
    assert isinstance(ResearchRunStore(":memory:"), RunStorePort)
    assert isinstance(
        PaperTrackStore(":memory:", create=True),
        PaperTrackStorePort,
    )
    assert isinstance(
        ForwardLedger(":memory:", create=True),
        ForwardLedgerPort,
    )


def test_run_registry_accepts_a_port_double_without_store_implementation():
    from services.research.run_registry.contract import ResearchRun
    from services.research.run_registry.registry import RunRegistry

    class PortDouble:
        def __init__(self):
            self.rows = {}

        def upsert(self, run):
            self.rows[run.run_id] = run

        def get(self, run_id):
            return self.rows.get(run_id)

        def list(self, kind=None, limit=200):
            rows = list(self.rows.values())
            if kind:
                rows = [row for row in rows if row.kind == kind]
            return rows[:limit]

        def set_status(self, run_id, status, error=None):
            from dataclasses import replace

            self.rows[run_id] = replace(
                self.rows[run_id],
                status=status,
                error=error,
            )

        def append_log(self, run_id, line):
            self.rows[run_id].log.append(line)

    port = PortDouble()
    registry = RunRegistry(port)
    run = ResearchRun(
        run_id="r1",
        kind="combo_book",
        title="combo",
        params={},
        status="queued",
        created_ts=1,
        finished_ts=None,
        metrics={},
        series={},
        verdict="",
        caveats=[],
        provenance="test",
    )
    registry.upsert(run)
    registry.set_status("r1", "running")
    registry.append_log("r1", "started")

    assert registry.get("r1").status == "running"
    assert registry.get("r1").log == ["started"]


def test_paper_domain_functions_accept_port_doubles():
    from services.research.paper_trading.advance import advance_paper_track
    from services.research.paper_trading.health import paper_health_by_alpha

    class PaperPortDouble:
        def __init__(self):
            self.rows = {}
            self.summaries = {}

        def append_periods(self, alpha_id, rows):
            self.rows.setdefault(alpha_id, []).extend(
                {
                    "as_of_ts": ts,
                    "realized_return": value,
                    "realized_ic": ic,
                    "source": source,
                }
                for ts, value, ic, source in rows
            )

        def periods(self, alpha_id):
            return list(self.rows.get(alpha_id, ()))

        def recompute_summary(self, alpha_id, *, backtest_sharpe=None):
            rows = self.rows.get(alpha_id, ())
            self.summaries[alpha_id] = {
                "alpha_id": alpha_id,
                "paper_ic": rows[-1]["realized_ic"] if rows else None,
                "cum_return": sum(row["realized_return"] for row in rows),
            }

        def track(self, alpha_id):
            return self.summaries.get(alpha_id)

        def all_tracks(self):
            return list(self.summaries.values())

    port = PaperPortDouble()
    assert (
        advance_paper_track(
            port,
            "a1",
            [0.01, 0.02, 0.03],
            [0.1, 0.2, 0.3],
            boundary=1,
        )
        == 2
    )
    health = paper_health_by_alpha(
        port,
        backtest_by_alpha={"a1": {"ic": 0.1, "return": 0.2}},
    )
    assert health["a1"]["live"]["ic"] == 0.3
