import json

import pytest

from tools.demo_execution_replay import main
from tests.research.test_demo_execution_journal import manifest
from tests.research.test_binance_order_receipts import native_spec


def test_offline_cli_import_and_read_only_recovery_have_identical_results(tmp_path, capsys):
    frozen, messages, journal, report = [tmp_path / n for n in ("manifest.json", "messages.json", "journal.db", "report.json")]
    frozen.write_text(json.dumps(manifest()))
    messages.write_text(json.dumps(native_spec()["raw_observed"]["messages"]))
    base = ["--journal", str(journal), "--report", str(report)]
    assert main(base + ["--manifest", str(frozen), "--messages", str(messages)]) == 0
    initial = report.read_bytes()
    before = journal.read_bytes()
    assert main(base) == 0
    assert report.read_bytes() == initial
    assert journal.read_bytes() == before
    assert json.loads(initial)["reconciliation"]["fee"] == "0.0198"
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["submission_enabled"] is False


def test_cli_malformed_input_never_overwrites_existing_report_or_leaks_input(tmp_path, capsys):
    frozen, report = tmp_path / "manifest.json", tmp_path / "report.json"
    frozen.write_text('{"private-text":"do-not-print","private-text":"duplicate"}')
    report.write_text("previous report")
    assert main(["--journal", str(tmp_path / "journal.db"), "--manifest", str(frozen), "--report", str(report)]) == 3
    assert "do-not-print" not in capsys.readouterr().out
    assert report.read_text() == "previous report"
    assert not (tmp_path / "journal.db").exists()


def test_cli_cannot_overwrite_its_journal_with_report(tmp_path):
    path = tmp_path / "journal.db"
    with pytest.raises(SystemExit):
        main(["--journal", str(path), "--report", str(path)])
    assert not path.exists()
