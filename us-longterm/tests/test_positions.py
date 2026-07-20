"""매입원가·평가 계산 — 브로커 앱 스타일 표시용."""
import json

import pytest

from longcore import positions


def _write_rebal(tmp_path, name, fills):
    (tmp_path / f"{name}.json").write_text(
        json.dumps({"fills": fills}, ensure_ascii=False), encoding="utf-8")


def test_cost_basis_single_buy(tmp_path):
    _write_rebal(tmp_path, "2026-07-20_init", [
        {"ticker": "VOO", "side": "BUY", "qty": 4.0, "price": 683.0,
         "gross_usd": 2732.0, "commission_usd": 2.73},
    ])
    cb = positions.cost_basis(tmp_path)
    assert cb["VOO"]["shares"] == pytest.approx(4.0)
    assert cb["VOO"]["cost_usd"] == pytest.approx(2734.73)   # 체결액 + 수수료


def test_cost_basis_accumulates_buys(tmp_path):
    _write_rebal(tmp_path, "2026-07-20_init", [
        {"ticker": "NVDA", "side": "BUY", "qty": 2.0, "price": 200.0,
         "gross_usd": 400.0, "commission_usd": 0.4}])
    _write_rebal(tmp_path, "2026-10-01_quarterly", [
        {"ticker": "NVDA", "side": "BUY", "qty": 1.0, "price": 220.0,
         "gross_usd": 220.0, "commission_usd": 0.22}])
    cb = positions.cost_basis(tmp_path)
    assert cb["NVDA"]["shares"] == pytest.approx(3.0)
    assert cb["NVDA"]["cost_usd"] == pytest.approx(620.62)


def test_cost_basis_sell_reduces_proportionally(tmp_path):
    """평균원가법 — 절반 매도하면 원가도 절반 차감."""
    _write_rebal(tmp_path, "2026-07-20_init", [
        {"ticker": "TSM", "side": "BUY", "qty": 4.0, "price": 400.0,
         "gross_usd": 1600.0, "commission_usd": 0.0}])
    _write_rebal(tmp_path, "2026-10-01_quarterly", [
        {"ticker": "TSM", "side": "SELL", "qty": 2.0, "price": 500.0,
         "gross_usd": 1000.0, "commission_usd": 0.0}])
    cb = positions.cost_basis(tmp_path)
    assert cb["TSM"]["shares"] == pytest.approx(2.0)
    assert cb["TSM"]["cost_usd"] == pytest.approx(800.0)     # 1600 의 절반


def test_fully_sold_excluded(tmp_path):
    _write_rebal(tmp_path, "a", [
        {"ticker": "X", "side": "BUY", "qty": 1.0, "price": 10.0,
         "gross_usd": 10.0, "commission_usd": 0.0},
        {"ticker": "X", "side": "SELL", "qty": 1.0, "price": 12.0,
         "gross_usd": 12.0, "commission_usd": 0.0}])
    assert "X" not in positions.cost_basis(tmp_path)


def test_evaluate_gain():
    ev = positions.evaluate(shares=10.0, price=110.0, cost_usd=1000.0, usdkrw=1400.0)
    assert ev["value_usd"] == pytest.approx(1100.0)
    assert ev["pnl_usd"] == pytest.approx(100.0)
    assert ev["ret_pct"] == pytest.approx(10.0)
    assert ev["value_krw"] == pytest.approx(1540000)
    assert ev["pnl_krw"] == pytest.approx(140000)


def test_evaluate_loss():
    ev = positions.evaluate(shares=5.0, price=80.0, cost_usd=500.0, usdkrw=1400.0)
    assert ev["pnl_usd"] == pytest.approx(-100.0)
    assert ev["ret_pct"] == pytest.approx(-20.0)


def test_superseded_subdir_ignored(tmp_path):
    """superseded/ 하위(폐기 설립 기록)는 안 읽는다."""
    _write_rebal(tmp_path, "2026-07-20_init", [
        {"ticker": "VOO", "side": "BUY", "qty": 1.0, "price": 100.0,
         "gross_usd": 100.0, "commission_usd": 0.0}])
    sub = tmp_path / "superseded"
    sub.mkdir()
    _write_rebal(sub, "2026-07-20_init_old", [
        {"ticker": "VOO", "side": "BUY", "qty": 99.0, "price": 100.0,
         "gross_usd": 9900.0, "commission_usd": 0.0}])
    cb = positions.cost_basis(tmp_path)
    assert cb["VOO"]["shares"] == pytest.approx(1.0)         # 폐기 기록 무시
