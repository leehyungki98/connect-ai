"""백테스트 루프 (일봉). 검증된 결정적 모듈 재사용: 스크리너·사이징·리스크 게이트·체결 모델.

LLM 브레인은 제외 — 결정적 기본 진입 규칙을 쓴다 (LLM 기여도는 모의 실운용에서 측정).
기본 진입 규칙:
- 스크리너 상위 top_n 중 미보유 종목을 순위대로 진입 (리스크 게이트 통과분만)
- 매수 지정가 = 전일 종가(호가 보정), 손절 = -stop_bp, 목표 = +target_bp
- horizon_bars 경과 시 시가 매도
룩어헤드 금지: t일 진입 판단(랭킹·지정가·시장폭)은 t-1일까지 데이터만 사용.
현금 음수 진입은 버그로 간주해 즉시 RuntimeError (불변 조건).

개선 파라미터 (2026-07 1년 백테스트 실패 패턴 분석에서 도출 — 기본값은 OFF,
기간 분할 비교로 검증 후에만 기본값 승격):
- P2 cooldown_bars: 손절 후 재진입 금지 거래일 (반복 채찍질 차단 — stop 손실 82%가 반복 종목)
- P1 stop_vol_k: 변동성 비례 손절 (고정 -5%가 노이즈에 잘림 — stop 58%가 1봉 컷).
  손절 거리 = clamp(k·σ20, 2%, 10%). 사이징이 (진입-손절) 기반이라 리스크 예산 불변.
- P3 market_breadth_min: 시장 폭 필터 — 유니버스 중 20일 이평 위 종목 비율이
  기준 미만이면 신규 진입 중단 (동시 손절일 37일에 stop 손실 52% 집중).
- P3' max_new_per_day: 일별 신규 진입 상한.
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

MIN_STOP_DIST = 0.02   # 변동성 비례 손절 하한
MAX_STOP_DIST = 0.10   # 변동성 비례 손절 상한


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
    stop_bp: int = 500       # 진입가 대비 -5% (stop_vol_k 미설정 시)
    target_bp: int = 1_000   # 진입가 대비 +10%
    horizon_bars: int = 10   # 보유 거래일 수
    cooldown_bars: int = 0                  # P2: 손절 후 재진입 금지 (0=off)
    stop_vol_k: float | None = None         # P1: 변동성 비례 손절 배수 (None=고정)
    market_breadth_min: float | None = None  # P3: 시장 폭 필터 (None=off)
    max_new_per_day: int | None = None       # P3': 일별 신규 진입 상한 (None=무제한)


# 2026-07-19 조건부 채택 (C2): 2년 백테스트에서 MDD 4/4 구간 개선 검증.
# 약세·횡보 국면 샤프 열위는 모의 운용 관찰 조건. V0 비교 기준은 BaselineParams() 유지.
ADOPTED_PARAMS = BaselineParams(
    cooldown_bars=10, stop_vol_k=2.5, market_breadth_min=0.5, max_new_per_day=2
)


@dataclass(frozen=True)
class TradeRecord:
    """청산 체결 1건 기록 — 실패 패턴 분석 재료 (부분체결은 각각 기록)."""
    symbol: str
    entry_date: str
    entry_price: int
    exit_date: str
    exit_price: int
    exit_kind: str      # "stop" | "target" | "time"
    qty: int
    bars_held: int
    pnl_krw: int        # 순손익 (수수료·세금·슬리피지 반영, 매수비용 비례 배분)
    pnl_pct: float      # 체결가 기준 exit/entry - 1


@dataclass
class _Pos:
    qty: int
    entry_price: int
    stop: int
    target: int
    entry_idx: int
    last_close: int
    entry_date: str = ""
    init_qty: int = 0
    buy_cost: int = 0   # 매수 총비용 (수수료 포함, 양수)


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: tuple[int, ...]
    trades: int              # 청산 완료 기준
    wins: int
    total_return: float      # 0.05 = +5%
    mdd: float
    sharpe: float
    trade_log: tuple[TradeRecord, ...] = ()


def _stop_price(limit: int, vol20: float, params: BaselineParams) -> int:
    """손절가. P1 stop_vol_k 설정 시 변동성 비례, 아니면 고정 stop_bp."""
    if params.stop_vol_k is not None:
        dist = min(max(params.stop_vol_k * vol20, MIN_STOP_DIST), MAX_STOP_DIST)
        return int(limit * (1 - dist))
    return limit * (10_000 - params.stop_bp) // 10_000


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
    trade_log: list[TradeRecord] = []
    cooldown_until: dict[str, int] = {}   # P2: 손절 종목 → 재진입 가능 인덱스

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
            kind = "stop"
            fill = fill_stop_sell(sym, p.stop, p.qty, bar.open, bar.low, bar.volume)
            if fill is None:
                kind = "target"
                fill = fill_limit_sell(sym, p.target, p.qty, bar.open, bar.high, bar.volume)
            if fill is None and t - p.entry_idx >= params.horizon_bars:
                kind = "time"
                fill = fill_market_sell(sym, p.qty, bar.open, bar.volume)
            if fill is None:
                continue
            cash += fill.net_krw
            p.qty -= fill.qty
            cost_share = p.buy_cost * fill.qty // p.init_qty if p.init_qty else 0
            trade_log.append(TradeRecord(
                sym, p.entry_date, p.entry_price, d, fill.price, kind,
                fill.qty, t - p.entry_idx, fill.net_krw - cost_share,
                fill.price / p.entry_price - 1,
            ))
            if p.qty <= 0:
                trades += 1
                if fill.price > p.entry_price:
                    wins += 1
                if kind == "stop":
                    cooldown_until[sym] = t + params.cooldown_bars  # P2
                del positions[sym]

        # --- 3. 진입 (지정가 = 전일 종가, 오늘 봉으로 체결 판정) ---
        allow_new = True
        if params.market_breadth_min is not None:  # P3: 시장 폭 (t-1 데이터)
            eligible = [h for h in history.values() if len(h) >= 20]
            if eligible:
                above = sum(1 for h in eligible if h[-1] > sum(h[-20:]) / 20)
                allow_new = above / len(eligible) >= params.market_breadth_min

        equity_mark = cash + sum(p.qty * p.last_close for p in positions.values())
        new_today = 0
        for r in ranked:
            if not allow_new:
                break
            if params.max_new_per_day is not None and new_today >= params.max_new_per_day:
                break
            if r.symbol in positions:
                continue
            if t < cooldown_until.get(r.symbol, 0):  # P2: 쿨다운 중
                continue
            bar = by_date[r.symbol].get(d)
            if bar is None:
                continue
            limit = round_down_to_tick(history[r.symbol][-1])
            stop = _stop_price(limit, r.vol20, params)
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
            positions[r.symbol] = _Pos(
                fill.qty, fill.price, stop, target, t, fill.price,
                entry_date=d, init_qty=fill.qty, buy_cost=-fill.net_krw,
            )
            new_today += 1

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
        trade_log=tuple(trade_log),
    )
