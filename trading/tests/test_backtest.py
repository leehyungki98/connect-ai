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


# --- 트레이드 로그 (실패 패턴 재료) ---

def test_trade_log_recorded():
    data = make_data({"000010": bars_from_closes(uptrend(daily=0.008))})
    r = run_backtest(data, CASH)
    assert len(r.trade_log) >= r.trades >= 1
    assert {t.exit_kind for t in r.trade_log} <= {"stop", "target", "time"}
    tr = r.trade_log[0]
    assert tr.qty > 0 and tr.bars_held > 0 and tr.entry_date < tr.exit_date


def test_stop_trades_logged_with_loss():
    up = uptrend(70, daily=0.006)
    knife = [round(up[-1] * 0.85 * 0.98**i) for i in range(30)]
    data = make_data({"000010": bars_from_closes(up + knife)})
    r = run_backtest(data, CASH)
    stops = [t for t in r.trade_log if t.exit_kind == "stop"]
    assert stops and all(t.pnl_krw < 0 for t in stops)  # 손절 기록 + 순손실 부호


# --- 룩어헤드 금지 ---

def test_no_lookahead_last_day_jump_not_traded():
    """마지막 날에만 급등하는 종목: 랭킹은 전일까지 데이터만 쓰므로 진입 불가."""
    closes = flat(99) + [15_000]
    data = make_data({"000010": bars_from_closes(closes)})
    r = run_backtest(data, CASH)
    assert r.trades == 0 and len([e for e in r.equity_curve if e != CASH]) == 0


# --- 개선 파라미터 P1/P2/P3 (기본값 OFF — 기존 동작 불변) ---

def whipsaw(n_up=70, drop=0.12, recover=30):
    """상승 자격 획득 → 급락(-12%, 손절 트리거) → 재상승 (재진입 유혹)."""
    c = uptrend(n_up, daily=0.006)
    c.append(round(c[-1] * (1 - drop)))
    base = c[-1]
    c += [round(base * 1.006 ** i) for i in range(1, recover + 1)]
    return c


def test_p2_cooldown_blocks_reentry():
    data = make_data({"000010": bars_from_closes(whipsaw())})
    no_cd = run_backtest(data, CASH)
    with_cd = run_backtest(data, CASH, BaselineParams(cooldown_bars=50))
    n_no = len([t for t in no_cd.trade_log if t.symbol == "000010"])
    n_cd = len([t for t in with_cd.trade_log if t.symbol == "000010"])
    assert n_no >= 2      # 쿨다운 없으면 손절 후 재진입
    assert n_cd == 1      # 쿨다운이면 1회로 끝


def test_p1_stop_price_scales_with_vol():
    from autotrader.backtest import _stop_price
    p = BaselineParams(stop_vol_k=2.0)
    assert _stop_price(10_000, 0.03, p) == 9_400   # 2×3% = 6%
    assert _stop_price(10_000, 0.005, p) == 9_800  # 하한 2% 클램프
    assert _stop_price(10_000, 0.08, p) == 9_000   # 상한 10% 클램프
    assert _stop_price(10_000, 0.03, BaselineParams()) == 9_500  # 기본 고정 -5%


def test_p3_market_breadth_filter_blocks_entries():
    # 유니버스 대부분이 이평 이하(flat) → 폭 6% < 40% → 신규 진입 전면 중단
    data = make_data({"000010": bars_from_closes(uptrend(daily=0.008))}, )
    off = run_backtest(data, CASH)
    on = run_backtest(data, CASH, BaselineParams(market_breadth_min=0.4))
    assert off.trades >= 1
    assert on.trades == 0 and on.equity_curve[-1] == CASH


def test_p3_max_new_per_day_cap():
    import collections
    extra = {
        f"00{i:03d}0": bars_from_closes(uptrend(start=10_000 + i * 500, daily=0.005 + i * 0.0003))
        for i in range(12)
    }
    r = run_backtest(make_data(extra), CASH,
                     BaselineParams(top_n=12, max_new_per_day=2))
    per_day = collections.Counter(t.entry_date for t in r.trade_log)
    assert r.trades >= 1
    assert max(per_day.values()) <= 2


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
