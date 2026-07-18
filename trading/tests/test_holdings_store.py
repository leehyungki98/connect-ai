import pytest

from autotrader.execution.holdings import HoldingMeta, HoldingsStore, reconcile
from autotrader.gates.types import Position

META = HoldingMeta(66_000, 78_000, 10, "2026-07-15")


def test_save_load_roundtrip(tmp_path):
    s = HoldingsStore(tmp_path / "h.json")
    s.save({"005930": META})
    assert HoldingsStore(tmp_path / "h.json").load() == {"005930": META}


def test_missing_file_empty(tmp_path):
    assert HoldingsStore(tmp_path / "h.json").load() == {}


def test_corrupted_file_fails_closed(tmp_path):
    f = tmp_path / "h.json"
    f.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="fail-closed"):
        HoldingsStore(f).load()


def test_reconcile_matches_kis_qty():
    holdings, pruned, unmanaged = reconcile(
        {"005930": META}, {"005930": Position(7, 490_000)}
    )
    assert holdings[0].qty == 7  # 수량은 KIS 기준
    assert holdings[0].stop_price == 66_000
    assert pruned == {"005930": META} and unmanaged == []


def test_reconcile_prunes_closed_positions():
    holdings, pruned, unmanaged = reconcile({"005930": META}, {})
    assert holdings == [] and pruned == {}  # 잔고에 없으면 메타 제거


def test_reconcile_reports_unmanaged():
    holdings, pruned, unmanaged = reconcile({}, {"000660": Position(5, 1_000_000)})
    assert holdings == [] and unmanaged == ["000660"]
