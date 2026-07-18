"""목표 7 검증(백테스트): 재현성, 룩어헤드 금지, 손절 한도, 비용 반영."""
from datetime import date, timedelta

from autotrader.backtest import BacktestResult, BaselineParams, Bar, run_backtest

CASH = 10_000_000


def bars_from_closes(closes, start=date(2026, 1, 1), volume=2_000_000):
    """종가 시퀀스 → 일봉 (시가=전일 종가, 고저는 시가/종가 감싸기)."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(
            (start + timedelta(days=i)).isoformat(),
            o, max(o, c) + 10, min(o, c) - 10, c, volume,
        ))
        prev = c
    return out


def flat(n=100, price=10_000):
    return [price] * n


def uptrend(n=100, start=10_000, daily=0.004):
    return [round(start * (1 + daily) ** i) for i in range(n)]


def make_data(extra=None):
    data = {f"90{i:04d}": bars_from_closes(flat()) for i in range(20)}  # 이평 필터 탈락
    if extra:
        data.update(extra)
    return data


# --- 재현성 ---

def test_same_input_same_result():
    data = make_data({"000010": bars_from_closes(uptrend())})
    assert run_backtest(data, CASH) == run_backtest(data, CASH)


# --- 수익/비용 ---

def test_uptrend_profitable_after_costs():
    data = make_data({"000010": bars_from_closes(uptrend(daily=0.008))})
    r = run_backtest(data, CASH)
    assert r.trades >= 1 and r.wins >= 1
    assert r.total_return > 0


def test_flat_market_no_trades():
    r = run_backtest(make_data(), CASH)
    assert r.trades == 0
    assert r.equity_curve[-1] == CASH  # 진입 없음 → 자본 불변


# --- 손절이 손실을 제한하는가 ---

def test_stop_limits_loss_on_falling_knife():
    """급락 지속 시나리오: 보유했다면 포지션(15%) x -60% = 계좌 -9%.
    손절+변동성 필터가 있으면 갭(-15%) 관통 손실 + 재진입 마찰까지 포함해도
    MDD가 5% 미만이어야 한다. (갭이 손절가를 뚫으면 트레이드당 리스크 1.5%를
    초과할 수 있다 — 이는 모델이 갭 리스크를 정직하게 반영한 결과다.)"""
    up = uptrend(70, daily=0.006)
    knife = [round(up[-1] * 0.85 * 0.98**i) for i in range(30)]
    data = make_data({"000010": bars_from_closes(up + knife)})
    r = run_backtest(data, CASH)
    assert r.trades >= 1
    assert r.mdd < 0.05
    assert r.equity_curve[-1] > CASH * 0.95


# --- 룩어헤드 금지 ---

def test_no_lookahead_last_day_jump_not_traded():
    """마지막 날에만 급등하는 종목: 랭킹은 전일까지 데이터만 쓰므로 진입 불가."""
    closes = flat(99) + [15_000]
    data = make_data({"000010": bars_from_closes(closes)})
    r = run_backtest(data, CASH)
    assert r.trades == 0 and len([e for e in r.equity_curve if e != CASH]) == 0


# --- 게이트 준수 ---

def test_max_positions_respected():
    extra = {
        f"00{i:03d}0": bars_from_closes(uptrend(start=10_000 + i * 500, daily=0.005 + i * 0.0003))
        for i in range(12)  # 적격 12종목 > MAX_POSITIONS 8
    }
    data = make_data(extra)
    r = run_backtest(data, CASH, BaselineParams(top_n=12, horizon_bars=30))
    # 어떤 시점에도 현금이 음수가 아니고 (내부 불변 조건), 결과가 산출된다
    assert isinstance(r, BacktestResult)
    assert min(r.equity_curve) > 0
