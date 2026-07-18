"""백테스트 루프 (일봉). 검증된 결정적 모듈 재사용: 스크리너·사이징·리스크 게이트·체결 모델.

LLM 브레인은 제외 — 결정적 기본 진입 규칙을 쓴다 (LLM 기여도는 모의 실운용에서 측정).
기본 진입 규칙:
- 스크리너 상위 top_n 중 미보유 종목을 순위대로 진입 (리스크 게이트 통과분만)
- 매수 지정가 = 전일 종가(호가 보정), 손절 = -stop_bp, 목표 = +target_bp
- horizon_bars 경과 시 시가 매도
룩어헤드 금지: t일 진입 판단(랭킹·지정가)은 t-1일까지 데이터만 사용.
현금 음수 진입은 버그로 간주해 즉시 RuntimeError (불변 조건).
"""
from __future__ import annotations

from dataclasses import dataclass

from autotrader.execution.fills import (
    COMMISSION_PPM,
    _ceil_ppm,
    fill_limit_buy,
    fill_limit_sell,
    fill_market_sell,
    fill_stop_sell,
)
from autotrader.execution.ticks import round_down_to_tick
from autotrader.gates.risk import check_risk
from autotrader.gates.types import OrderIntent, Portfolio, Position
from autotrader.metrics import max_drawdown_pct, sharpe_annualized
from autotrader.screener.ranking import MIN_HISTORY, SymbolData, rank
from autotrader.sizing import position_size


@dataclass(frozen=True)
class Bar:
    date: str   # ISO (YYYY-MM-DD)
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class BaselineParams:
    top_n: int = 10
    stop_bp: int = 500       # 진입가 대비 -5%
    target_bp: int = 1_000   # 진입가 대비 +10%
    horizon_bars: int = 10   # 보유 거래일 수


@dataclass
class _Pos:
    qty: int
    entry_price: int
    stop: int
    target: int
    entry_idx: int
    last_close: int


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: tuple[int, ...]
    trades: int              # 청산 완료 기준
    wins: int
    total_return: float      # 0.05 = +5%
    mdd: float
    sharpe: float


def run_backtest(
    data: dict[str, list[Bar]],
    initial_cash: int,
    params: BaselineParams = BaselineParams(),
) -> BacktestResult:
    dates = sorted({b.date for bars in data.values() for b in bars})
    by_date = {
        sym: {b.date: b for b in bars} for sym, bars in data.items()
    }
    history: dict[str, list[int]] = {sym: [] for sym in data}

    cash = initial_cash
    positions: dict[str, _Pos] = {}
    curve: list[int] = []
    trades = wins = 0

    for t, d in enumerate(dates):
        # --- 1. 랭킹 (t-1까지의 이력만 사용) ---
        universe = [
            SymbolData(sym, tuple(h)) for sym, h in history.items()
            if len(h) >= MIN_HISTORY
        ]
        ranked = rank(universe, params.top_n) if universe else []

        # --- 2. 청산 (오늘 봉으로 체결 판정) ---
        for sym in sorted(positions):
            bar = by_date[sym].get(d)
            if bar is None:  # 거래정지 등 — 보유 유지
                continue
            p = positions[sym]
            fill = fill_stop_sell(sym, p.stop, p.qty, bar.open, bar.low, bar.volume)
            if fill is None:
                fill = fill_limit_sell(sym, p.target, p.qty, bar.open, bar.high, bar.volume)
            if fill is None and t - p.entry_idx >= params.horizon_bars:
                fill = fill_market_sell(sym, p.qty, bar.open, bar.volume)
            if fill is None:
                continue
            cash += fill.net_krw
            p.qty -= fill.qty
            if p.qty <= 0:
                trades += 1
                if fill.price > p.entry_price:
                    wins += 1
                del positions[sym]

        # --- 3. 진입 (지정가 = 전일 종가, 오늘 봉으로 체결 판정) ---
        equity_mark = cash + sum(p.qty * p.last_close for p in positions.values())
        for r in ranked:
            if r.symbol in positions:
                continue
            bar = by_date[r.symbol].get(d)
            if bar is None:
                continue
            limit = round_down_to_tick(history[r.symbol][-1])
            stop = limit * (10_000 - params.stop_bp) // 10_000
            target = limit * (10_000 + params.target_bp) // 10_000
            pf_view = Portfolio(
                equity_mark, cash,
                {s: Position(p.qty, p.qty * p.last_close) for s, p in positions.items()},
            )
            qty = position_size(limit, stop, pf_view)
            while qty > 0 and qty * limit + _ceil_ppm(qty * limit, COMMISSION_PPM) > cash:
                qty -= 1  # 수수료 포함 현금 초과 방지
            if qty <= 0:
                continue
            intent = OrderIntent("buy", r.symbol, qty, limit, stop)
            if not check_risk(intent, pf_view).allowed:
                continue
            fill = fill_limit_buy(r.symbol, limit, qty, bar.open, bar.low, bar.volume)
            if fill is None:
                continue
            cash += fill.net_krw  # 음수 (대금+수수료)
            if cash < 0:
                raise RuntimeError(f"cash went negative on {d}: {cash}")
            positions[r.symbol] = _Pos(fill.qty, fill.price, stop, target, t, fill.price)

        # --- 4. 종가 반영 + 평가 ---
        for sym, h in history.items():
            bar = by_date[sym].get(d)
            if bar is not None:
                h.append(bar.close)
                if sym in positions:
                    positions[sym].last_close = bar.close
        curve.append(cash + sum(p.qty * p.last_close for p in positions.values()))

    equity = tuple(curve)
    total_return = (equity[-1] / initial_cash - 1) if equity else 0.0
    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        wins=wins,
        total_return=total_return,
        mdd=max_drawdown_pct(equity),
        sharpe=sharpe_annualized(equity),
    )
