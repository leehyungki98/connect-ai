"""장 시작 전 1회 실행 파이프라인 (모의 전용).

순서 (안전 우선):
1. KIS 잔고 조회(읽기 전용) → 당일 시작 평가액 기록/로드
2. SafetyGuard (킬스위치 + 일일 손실) — 차단이면 주문 없이 종료
3. 결정적 청산 (손절/목표/기간만료) + 브레인 청산 → 게이트 → 매도 주문
4. 스크리너 상위 후보 → 선정자(GPT/codex, 공격적) → 결정적 프리체크(K1/K2/K5)
   → 판정자(Claude, 기각 기준 K3/K4 — 통과가 기본값) → 결정적 사이징
   → 리스크·컴플라이언스 게이트 → 매수 주문
   (선정자/판정자 출력 무효 시 신규 진입 없음 — 이전 상태 유지)

C2 채택 파라미터 (2026-07-19, 2년 백테스트 검증 — MDD 4/4 구간 개선):
- 손절 후 재진입 쿨다운 ≈10거래일 (달력 14일 근사)
- 손절 거리 하한 = clamp(2.5·σ20, 2%, 10%) — LLM 손절이 더 좁으면 보정(넓힘).
  사이징이 (진입-손절) 기반이라 리스크 예산은 불변.
- 시장 폭 필터: 유니버스 중 20일 이평 위 비율 < 0.5면 신규 진입 중단 (청산은 계속)
- 일별 신규 진입 상한 2종목
관찰 조건부: 약세·횡보 국면 샤프 열위가 백테스트에서 확인됨 — 모의 운용에서 관찰.

낙관 편향 방지: 같은 실행 안에서 매수 누적 비용을 현금에서 차감해 추적하고,
매도 대금은 다음 실행 전까지 현금으로 계산하지 않는다.
당일 매도(손절 등)한 종목은 같은 실행에서 재진입 금지.
상태 파일 손상 시: 안전 관련(day_start/holdings)은 fail-closed(전체 차단),
쿨다운은 보호 장치가 아닌 제한 장치이므로 손상 시 빈 상태로 진행하되 경고 기록.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Sequence

from autotrader.backtest import MAX_STOP_DIST, MIN_STOP_DIST
from autotrader.brain.client import Candidate
from autotrader.brain.two_stage import run_two_stage
from autotrader.execution.fills import COMMISSION_PPM, _ceil_ppm
from autotrader.execution.holdings import HoldingMeta, HoldingsStore, reconcile
from autotrader.execution.stop_monitor import check_exits
from autotrader.execution.ticks import round_down_to_tick
from autotrader.gates.compliance import PAPER_BASE_URL, PAPER_TR_IDS, OrderEndpoint, check_compliance
from autotrader.gates.risk import check_risk
from autotrader.gates.types import OrderIntent, Portfolio, Position
from autotrader.safety.guard import SafetyGuard
from autotrader.screener.ranking import SymbolData, rank
from autotrader.sizing import position_size

SELL_DISCOUNT_PCT = 2      # 청산 지정가 = 현재가 -2% (체결 확률 확보)
COOLDOWN_CAL_DAYS = 14     # C2: 손절 후 재진입 금지 (달력일 ≈ 10거래일)
STOP_VOL_K = 2.5           # C2: 손절 거리 하한 배수
MARKET_BREADTH_MIN = 0.5   # C2: 시장 폭 필터
MAX_NEW_PER_DAY = 2        # C2: 일별 신규 진입 상한


@dataclass
class RunReport:
    date: str
    blocked: str | None = None
    exits_placed: list[str] = field(default_factory=list)
    buys_placed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    brain_ok: bool = False


def _day_start_equity(path: Path, today: date, current_equity: int) -> int | None:
    """당일 시작 평가액. 파일 손상 시 None (fail-closed)."""
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if str(raw["date"]) == today.isoformat():
                return int(raw["equity"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"date": today.isoformat(), "equity": current_equity}),
        encoding="utf-8",
    )
    tmp.replace(path)
    return current_equity


def _load_cooldowns(path: Path) -> tuple[dict[str, str], str | None]:
    """{symbol: 재진입 가능일 ISO}. 손상 시 빈 상태 + 경고 (제한 장치라 fail-open)."""
    if not path.exists():
        return {}, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.items()}, None
    except (json.JSONDecodeError, AttributeError, OSError) as e:
        return {}, f"cooldown state corrupted — 빈 상태로 진행: {e}"


def _save_cooldowns(path: Path, cooldowns: dict[str, str], today: date) -> None:
    pruned = {s: d for s, d in cooldowns.items() if d > today.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pruned, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _market_breadth(universe: Sequence[SymbolData]) -> float | None:
    """유니버스 중 20일 이평 위 종목 비율 (전일까지 데이터 — 룩어헤드 없음)."""
    eligible = [s for s in universe if len(s.closes) >= 20]
    if not eligible:
        return None
    above = sum(1 for s in eligible if s.closes[-1] > sum(s.closes[-20:]) / 20)
    return above / len(eligible)


def run_premarket(
    kis,
    guard: SafetyGuard,
    store: HoldingsStore,
    universe: Sequence[SymbolData],
    today: date,
    day_start_file: Path,
    cooldown_file: Path,
    proposer_brain: str = "codex",
    judge_brain: str = "claude",
    proposer_runner: Callable | None = None,
    judge_runner: Callable | None = None,
    top_n: int = 10,
) -> RunReport:
    report = RunReport(date=today.isoformat())

    # --- 1~2. 잔고 → 안전 게이트 ---
    pf = kis.get_portfolio()
    equity_start = _day_start_equity(day_start_file, today, pf.equity_krw)
    if equity_start is None:
        report.blocked = "day_start state corrupted (fail-closed)"
        return report
    decision = guard.check(today, equity_start, pf.equity_krw)
    if not decision.allowed:
        report.blocked = decision.reason
        return report

    try:
        metas = store.load()
    except RuntimeError as e:
        report.blocked = str(e)
        return report
    holdings, metas, unmanaged = reconcile(metas, pf.positions)
    for sym in unmanaged:
        report.skipped.append(f"{sym}: 메타 없는 보유 (수동 확인 필요)")

    cooldowns, cd_warn = _load_cooldowns(cooldown_file)
    if cd_warn:
        report.skipped.append(cd_warn)

    # --- 3. 청산: 결정적 신호 + 브레인 exits ---
    prices = {h.symbol: kis.get_price(h.symbol) for h in holdings}
    exit_signals = check_exits(holdings, prices, today)
    sold: set[str] = set()

    ranked = rank(universe, top_n)
    closes = {s.symbol: s.closes[-1] for s in universe if len(s.closes) > 0}
    candidates = [Candidate(r, closes[r.symbol]) for r in ranked]
    cand_by_symbol = {c.ranked.symbol: c for c in candidates}
    ts = run_two_stage(
        candidates, pf, proposer_brain, judge_brain, proposer_runner, judge_runner
    )
    report.brain_ok = ts.ok
    if not ts.ok:
        report.skipped.append(f"proposer rejected: {'; '.join(ts.errors[:3])}")
    elif ts.errors:
        report.skipped.append(f"judge error: {'; '.join(ts.errors[:2])}")
    for rj in ts.rejected:
        report.skipped.append(f"{rj.symbol} 판정 기각({rj.criterion}): {rj.reason}")

    exit_queue = [(s.symbol, s.qty, s.kind) for s in exit_signals]
    if ts.ok:
        for e in ts.exits:
            if e.symbol not in {q[0] for q in exit_queue} and e.symbol in pf.positions:
                exit_queue.append((e.symbol, pf.positions[e.symbol].qty, "brain"))

    for sym, qty, kind in exit_queue:
        price = prices.get(sym) or kis.get_price(sym)
        limit = round_down_to_tick(price * (100 - SELL_DISCOUNT_PCT) // 100)
        intent = OrderIntent("sell", sym, qty, limit)
        for gate_name, ok, reason in _run_gates(intent, pf):
            if not ok:
                report.skipped.append(f"{sym} sell({kind}): {gate_name} {reason}")
                break
        else:
            result = kis.place_order(intent, pf)
            if result.success:
                report.exits_placed.append(f"{sym} x{qty} ({kind}) @{limit}")
                sold.add(sym)
                if kind == "stop":  # C2: 쿨다운 시작
                    until = (today + timedelta(days=COOLDOWN_CAL_DAYS)).isoformat()
                    cooldowns[sym] = until
            else:
                report.skipped.append(f"{sym} sell({kind}): order failed {result.message}")

    # --- 4. 신규 진입 (판정 통과분만, C2 제약 적용) ---
    breadth = _market_breadth(universe)
    entries_blocked = breadth is not None and breadth < MARKET_BREADTH_MIN
    if entries_blocked:
        report.skipped.append(
            f"시장 폭 {breadth:.0%} < {MARKET_BREADTH_MIN:.0%} — 신규 진입 중단 (C2)"
        )

    if ts.ok and not entries_blocked:
        cash_left = pf.cash_krw
        positions_view = dict(pf.positions)
        new_today = 0
        for d in ts.entries:
            if new_today >= MAX_NEW_PER_DAY:
                report.skipped.append(
                    f"{d.symbol} buy: 일별 신규 진입 상한({MAX_NEW_PER_DAY}) 도달"
                )
                continue
            if d.symbol in sold:
                report.skipped.append(f"{d.symbol} buy: 당일 매도 종목 재진입 금지")
                continue
            if d.symbol in cooldowns and today.isoformat() < cooldowns[d.symbol]:
                report.skipped.append(
                    f"{d.symbol} buy: 손절 쿨다운 중 (~{cooldowns[d.symbol]})"
                )
                continue
            entry = round_down_to_tick(d.entry_price)
            # C2: 손절 거리 하한 보정 — LLM 손절이 2.5σ보다 좁으면 넓힌다
            cand = cand_by_symbol[d.symbol]
            min_dist = min(max(STOP_VOL_K * cand.ranked.vol20, MIN_STOP_DIST), MAX_STOP_DIST)
            stop_floor = int(entry * (1 - min_dist))
            stop = min(d.stop_price, stop_floor)
            if stop != d.stop_price:
                report.skipped.append(
                    f"{d.symbol}: 손절 보정 {d.stop_price}→{stop} (2.5σ 하한, 주문은 진행)"
                )
            pf_view = Portfolio(pf.equity_krw, cash_left, positions_view)
            existing = positions_view.get(d.symbol)
            qty = position_size(entry, stop, pf_view,
                                existing.value_krw if existing else 0)
            if qty <= 0:
                report.skipped.append(f"{d.symbol} buy: 사이징 0")
                continue
            intent = OrderIntent("buy", d.symbol, qty, entry, stop)
            rejected = False
            for gate_name, ok, reason in _run_gates(intent, pf_view):
                if not ok:
                    report.skipped.append(f"{d.symbol} buy: {gate_name} {reason}")
                    rejected = True
                    break
            if rejected:
                continue
            result = kis.place_order(intent, pf_view)
            if not result.success:
                report.skipped.append(f"{d.symbol} buy: order failed {result.message}")
                continue
            report.buys_placed.append(f"{d.symbol} x{qty} @{entry}")
            new_today += 1
            cost = qty * entry
            cash_left -= cost + _ceil_ppm(cost, COMMISSION_PPM)
            prev_value = existing.value_krw if existing else 0
            prev_qty = existing.qty if existing else 0
            positions_view[d.symbol] = Position(prev_qty + qty, prev_value + cost)
            metas[d.symbol] = HoldingMeta(
                stop, d.target_price, d.horizon_days, today.isoformat()
            )

    store.save(metas)
    _save_cooldowns(cooldown_file, cooldowns, today)
    return report


def _run_gates(intent: OrderIntent, pf: Portfolio):
    """리스크 → 컴플라이언스. (게이트명, 통과여부, 사유) 순차 반환."""
    r = check_risk(intent, pf)
    yield "risk:", r.allowed, r.reason
    ep = OrderEndpoint(PAPER_BASE_URL, PAPER_TR_IDS.get(intent.side, ""))
    c = check_compliance(intent, ep, pf)
    yield "compliance:", c.allowed, c.reason
