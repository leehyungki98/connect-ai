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

멱등성: order_ledger_file 을 주면 date-symbol-side 단위로 주문을 기록하고,
같은 거래일 재실행에서는 이미 시도된 주문을 다시 내지 않는다 (복구만).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Sequence

from autotrader.backtest import MAX_STOP_DIST, MIN_STOP_DIST
from autotrader.brain.client import Candidate
from autotrader.brain.two_stage import run_two_stage
from autotrader.execution.fills import COMMISSION_PPM, _ceil_ppm
from autotrader.execution.holdings import HoldingMeta, HoldingsStore, reconcile
from autotrader.execution.order_ledger import LedgerCorrupted, OrderLedger
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

# 섀도 전용 확장 선정 개수. 실계좌와 무관 — 주문 경로에서 절대 참조하지 않는다.
# 0 이면 확장 호출 자체를 안 한다(비용 0). 2026-07-21 사용자 승인으로 5.
SHADOW_WIDE_N = 5


def _shadow_wide_entries(candidates, pf, proposer_brain, judge_brain,
                         proposer_runner, judge_runner, rank_of, already):
    """섀도 기록용으로만 쓰는 확장 선정 — 선정자에게 최소 N개를 요구해 다시 받는다.

    실계좌가 쓴 결과(ts)와 완전히 분리된 두 번째 호출이다. 여기서 나온 어떤 값도
    주문·게이트·원장으로 흘러가지 않는다 — 반환값은 record_samples 로만 간다.
    already: 실계좌 선정에 이미 들어간 종목(중복 기록 방지).

    판정자까지 실계좌와 똑같이 태운다. 여기서 판정을 건너뛰면 나중에 성적이 나빠도
    '순위가 낮아서'인지 '판정을 안 거쳐서'인지 구분할 수 없다 — 비교가 성립하지 않는다.
    """
    from autotrader.brain.two_stage import run_two_stage as _rts

    wide = _rts(candidates, pf, proposer_brain, judge_brain,
                proposer_runner, judge_runner, want=SHADOW_WIDE_N)
    if not wide.ok:
        return []
    out = []
    for d in wide.entries:
        if d.symbol in already:
            continue      # 실계좌 선정분은 origin='선정자'로 이미 기록됐다
        out.append({"symbol": d.symbol, "entry_price": d.entry_price,
                    "stop_price": d.stop_price, "target_price": d.target_price,
                    "horizon_days": d.horizon_days, "rank": rank_of.get(d.symbol)})
    return out


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
    order_ledger_file: Path | None = None,
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

    # 주문 멱등성 원장 — 손상 시 중복 여부를 알 수 없으므로 fail-closed
    try:
        ledger = OrderLedger(order_ledger_file, today)
    except LedgerCorrupted as e:
        report.blocked = f"order ledger corrupted (fail-closed): {e}"
        return report

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
        prior = ledger.attempted(sym, "sell")
        if prior is not None:  # 재실행: 신규 주문 없이 상태만 복구
            report.skipped.append(f"{sym} sell({kind}): 당일 주문 기록 있음 — 재주문 생략")
            sold.add(sym)
            if prior.get("kind") == "stop":
                cooldowns[sym] = (today + timedelta(days=COOLDOWN_CAL_DAYS)).isoformat()
            continue
        price = prices.get(sym) or kis.get_price(sym)
        limit = round_down_to_tick(price * (100 - SELL_DISCOUNT_PCT) // 100)
        intent = OrderIntent("sell", sym, qty, limit)
        for gate_name, ok, reason in _run_gates(intent, pf):
            if not ok:
                report.skipped.append(f"{sym} sell({kind}): {gate_name} {reason}")
                break
        else:
            ledger.mark_sending(sym, "sell", qty=qty, price=limit, kind=kind)
            result = kis.place_order(intent, pf)
            ledger.mark_result(sym, "sell", result.success, result.message)
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

    # 섀도북: 판정자 통과분을 페이퍼로 기록 (주문 0). 폭 차단이라 실제로 못 사는 날에도
    # "샀다면 어땠을지"를 백테스트와 동일 잣대로 남긴다. 순수 기록 — 실매매 무영향.
    # try/except 전체 감쌈: 섀도 실패가 실제 파이프라인을 절대 못 죽인다.
    try:
        # PYTEST 중엔 섀도 기록 스킵 — 파이프라인 테스트가 실 ledger 를 오염시키지 않게.
        # (섀도 자체 단위테스트는 격리 경로로 record_entries 를 직접 호출한다.)
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            from autotrader import shadow
            # 폭 일지는 판정자 성공 여부와 무관하게 매일 남긴다 (사후 재구성 불가).
            shadow.log_breadth(today.isoformat(), breadth)
        if ts.ok and not os.environ.get("PYTEST_CURRENT_TEST"):
            from autotrader import shadow
            _vol20 = {c.ranked.symbol: c.ranked.vol20 for c in candidates}
            _sh_entries = [
                {"symbol": d.symbol, "entry_price": d.entry_price,
                 "stop_price": d.stop_price, "target_price": d.target_price,
                 "horizon_days": d.horizon_days}
                for d in ts.entries
            ]
            # 랭킹 전체 이름을 한 번에 (캐시라 두 번 부를 이유가 없다)
            _rank_names = shadow.resolve_names([c.ranked.symbol for c in candidates])
            _names = {d.symbol: _rank_names.get(d.symbol, d.symbol) for d in ts.entries}
            _today = today.isoformat()
            _rank_of = {c.ranked.symbol: i + 1 for i, c in enumerate(candidates)}
            for _e in _sh_entries:
                _e["rank"] = _rank_of.get(_e["symbol"])
            # ① 계좌 — 실계좌 제약 그대로 (카드에 보이는 그것)
            shadow.record_entries(_today, _sh_entries, _vol20,
                                  breadth=breadth, names=_names)
            # ② 샘플 — 제약 없이 전부 (폭 구간별 표본을 빨리 쌓으려고)
            shadow.record_samples(_today, _sh_entries, _vol20,
                                  breadth=breadth, names=_names, origin="선정자")
            # ③ 랭킹 일지 — 상위 10개 중 안 뽑힌 것까지 전부. 공짜이고, '그날 몇 등'은
            #    사후 재구성이 안 된다. 손절가가 없어 섀도 매매는 못 돌리지만
            #    "순위가 실제 수익과 상관있나"는 나중에 일봉만 붙이면 답이 나온다.
            _proposed = {d.symbol for d in ts.entries}
            shadow.log_ranking(_today, [
                {"symbol": c.ranked.symbol, "name": _rank_names.get(c.ranked.symbol),
                 "rank": i + 1, "close": c.last_close,
                 "ret20": round(c.ranked.ret20, 4), "ret60": round(c.ranked.ret60, 4),
                 "vol20": round(c.ranked.vol20, 4),
                 "proposed": c.ranked.symbol in _proposed}
                for i, c in enumerate(candidates)
            ], breadth=breadth)
            # ④ 확장 선정 — 섀도 전용 별도 호출. 실계좌가 쓴 ts 는 손대지 않는다.
            #    선정자가 스스로 고르면 하루 2개쯤이라 표본이 너무 느리게 쌓인다.
            #    "3~5등까지 샀으면 어땠나"를 재려면 그 제안 자체가 있어야 한다.
            #    실패해도 위 ①~③ 은 이미 기록됐다 (별도 try).
            #    확장 결과가 0건일 수 있는 이유가 둘이다 — 선정자가 이미 다 뽑아서
            #    새로 추가할 게 없거나, 호출이 터졌거나. 그냥 삼키면 이 둘을 구분할 수
            #    없어 '확장이 도는 중'이라고 착각하게 된다. 매일 결과를 남긴다.
            if SHADOW_WIDE_N > 0:
                _w = {"asked": SHADOW_WIDE_N, "got": 0, "new": 0, "error": None}
                try:
                    _wide = _shadow_wide_entries(
                        candidates, pf, proposer_brain, judge_brain,
                        proposer_runner, judge_runner, _rank_of, _proposed)
                    _w["got"] = len(ts.entries)      # 실계좌 선정이 이미 뽑은 수
                    _w["new"] = len(_wide)           # 그 위에 확장이 더한 수
                    if _wide:
                        shadow.record_samples(_today, _wide, _vol20, breadth=breadth,
                                              names=_rank_names, origin="확장")
                except Exception as _e:  # noqa: BLE001
                    _w["error"] = f"{type(_e).__name__}: {_e}"[:200]
                shadow.log_wide(_today, _w)
    except Exception:  # noqa: BLE001 — 표시/기록 실패는 절대 실매매를 막지 않는다
        pass

    if ts.ok and not entries_blocked:
        cash_left = pf.cash_krw
        positions_view = dict(pf.positions)
        new_today = 0
        for d in ts.entries:
            prior = ledger.attempted(d.symbol, "buy")
            if prior is not None:  # 재실행: 신규 주문 없이 노출·메타만 복구
                report.skipped.append(f"{d.symbol} buy: 당일 주문 기록 있음 — 재주문 생략")
                new_today += 1  # 이미 낸 주문도 일별 상한에 든다
                prior_qty = int(prior.get("qty", 0))
                prior_cost = prior_qty * int(prior.get("price", 0))
                cash_left -= prior_cost + _ceil_ppm(prior_cost, COMMISSION_PPM)
                existing = positions_view.get(d.symbol)
                positions_view[d.symbol] = Position(
                    (existing.qty if existing else 0) + prior_qty,
                    (existing.value_krw if existing else 0) + prior_cost,
                )
                if d.symbol not in metas and prior.get("stop") is not None:
                    metas[d.symbol] = HoldingMeta(
                        int(prior["stop"]), int(prior["target"]),
                        int(prior["horizon"]), today.isoformat(),
                    )
                continue
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
            ledger.mark_sending(d.symbol, "buy", qty=qty, price=entry, stop=stop,
                                target=d.target_price, horizon=d.horizon_days)
            result = kis.place_order(intent, pf_view)
            ledger.mark_result(d.symbol, "buy", result.success, result.message)
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
