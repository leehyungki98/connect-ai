"""데스크 배분 — 계좌 잔고(1,000만)가 아니라 스윙 지분(250만)으로 굴린다.

KIS 모의계좌 잔고는 줄일 수단이 없어서 코드로 축소한다. 여기가 틀리면
설계상 250만원인 데스크가 조용히 1,000만원을 굴린다 (2026-07-22 실제 사고).
"""
from autotrader.config import ACCOUNT_BASELINE_KRW, SWING_ALLOCATION_KRW
from autotrader.gates.types import Portfolio, Position
from autotrader.pipeline import desk_portfolio


def test_fresh_account_scales_to_allocation():
    """아무것도 안 산 상태 — 1,000만 계좌가 250만 데스크로 보여야 한다."""
    d = desk_portfolio(Portfolio(10_000_000, 10_000_000, {}))
    assert d.equity_krw == SWING_ALLOCATION_KRW == 2_500_000
    assert d.cash_krw == 2_500_000


def test_profit_compounds_into_desk_equity():
    """번 돈은 지분에 쌓인다 — 배분액을 고정 상한으로 쓰면 복리가 죽는다.

    이 계좌는 스윙 외 매매가 없으므로 계좌 손익 전부가 스윙 손익이다.
    """
    d = desk_portfolio(Portfolio(10_300_000, 10_300_000, {}))
    assert d.equity_krw == 2_800_000      # 250만 + 30만
    assert d.cash_krw == 2_800_000


def test_loss_shrinks_desk_equity():
    d = desk_portfolio(Portfolio(9_600_000, 9_600_000, {}))
    assert d.equity_krw == 2_100_000      # 250만 − 40만


def test_holdings_reduce_cash_not_equity():
    """100만원어치 보유 중 — 지분은 250만 유지, 쓸 수 있는 현금만 150만."""
    pos = {"005930": Position(10, 1_000_000)}
    d = desk_portfolio(Portfolio(10_000_000, 9_000_000, pos))
    assert d.equity_krw == 2_500_000
    assert d.cash_krw == 1_500_000
    assert d.positions == pos             # 보유는 손대지 않는다


def test_cash_never_negative_when_over_allocated():
    """배분액을 넘게 들고 있으면 현금 0 — 신규 진입만 멈추고 보유는 유지."""
    pos = {"005930": Position(10, 4_000_000)}
    d = desk_portfolio(Portfolio(10_000_000, 6_000_000, pos))
    assert d.cash_krw == 0                # 음수로 새지 않는다
    assert d.positions == pos             # 강제 청산 같은 건 하지 않는다


def test_desk_cash_never_exceeds_real_cash():
    """실계좌에 없는 현금을 만들어내면 안 된다 — 주문이 거부된다."""
    pos = {"005930": Position(10, 9_500_000)}
    d = desk_portfolio(Portfolio(10_000_000, 500_000, pos))
    assert d.cash_krw <= 500_000


def test_baseline_matches_actual_account():
    """기준액이 실제 계좌 최초 잔고와 어긋나면 지분 계산이 통째로 틀어진다."""
    from autotrader.config import INITIAL_CAPITAL_KRW
    assert ACCOUNT_BASELINE_KRW == INITIAL_CAPITAL_KRW
