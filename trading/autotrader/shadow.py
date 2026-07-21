"""섀도북 — 스윙 전략의 페이퍼 포트폴리오. 주문 0, 근데 진입→청산을 실매매처럼 추적.

목적: 시장 폭 필터에 막혀 실제로는 아무것도 못 사는 날에도, "샀다면 어땠을지"를
백테스트와 **동일 잣대**로 추적해 기록한다 (진입가·손절·목표·보유기간 → 청산 시각·
사유·실현손익). 나중에 폭 구간별로 "이 폭에선 이 정도 하면 이득이 이 정도"가 쌓인다.

경계:
- 이 모듈은 편집 금지 구역이 아니다 (autotrader/shadow.py). safety/gates/config 무접촉.
- 순환 import 방지: pipeline 을 import 하지 않는다 (pipeline 이 이걸 import). 전략 상수는
  여기 재정의하고 tests 가 drift 를 잡는다. 체결·사이징·틱은 leaf 함수 재사용 → 실제와 동일.
- 순수 기록. 주문 없음. 실제(모의) 매매 행동은 하나도 안 바꾼다.
"""
import json
from pathlib import Path

from autotrader.backtest import MAX_STOP_DIST, MIN_STOP_DIST
from autotrader.execution.fills import (COMMISSION_PPM, _ceil_ppm,
                                        fill_limit_sell, fill_market_sell,
                                        fill_stop_sell)
from autotrader.execution.ticks import round_down_to_tick
from autotrader.gates.types import Portfolio, Position
from autotrader.sizing import position_size

_BASE = Path(__file__).resolve().parents[1]
STATE_FILE = _BASE / "state" / "shadow_state.json"          # 운영 데이터 (커밋 제외)
NAMES_FILE = _BASE / "state" / "symbol_names.json"          # 코드→종목명 캐시
LEDGER_DIR = _BASE / "ledger" / "shadow"                    # 학습 자산 (git 추적)
TRADES_FILE = LEDGER_DIR / "shadow_trades.jsonl"            # 진입·청산 이벤트 이력


def resolve_names(codes, lookup=None) -> dict:
    """6자리 코드 → 종목명. state/symbol_names.json 캐시 (없는 것만 조회).
    lookup(code)->name 미제공 시 pykrx. 실패는 코드 그대로 (절대 예외 안 냄)."""
    names = {}
    if NAMES_FILE.exists():
        try:
            names = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            names = {}
    missing = [c for c in codes if c not in names]
    if missing:
        if lookup is None:
            try:
                from pykrx import stock
                lookup = stock.get_market_ticker_name
            except Exception:  # noqa: BLE001
                lookup = lambda c: c  # noqa: E731
        for c in missing:
            try:
                names[c] = lookup(c) or c
            except Exception:  # noqa: BLE001
                names[c] = c
        NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return {c: names.get(c, c) for c in codes}

# 전략 상수 — pipeline 과 동기화 대상 (tests/test_shadow.py 가 drift 를 잡는다).
INITIAL_CAPITAL_KRW = 10_000_000
MAX_NEW_PER_DAY = 2            # C2 일별 신규 진입 상한
STOP_VOL_K = 2.5              # C2 손절 거리 하한 배수
COOLDOWN_BARS = 10           # C2 손절 후 재진입 금지 거래일


def _fresh_state() -> dict:
    return {"cash_krw": INITIAL_CAPITAL_KRW, "positions": {}, "cooldowns": {},
            "last_date": None}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return _fresh_state()


def save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _append_trade(rec: dict) -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def adjusted_stop(entry: int, stop_price: int, vol20: float) -> int:
    """C2 손절 하한 보정 — 파이프라인과 동일: min(LLM손절, entry*(1-clamp(2.5σ,2%,10%)))."""
    min_dist = min(max(STOP_VOL_K * vol20, MIN_STOP_DIST), MAX_STOP_DIST)
    stop_floor = int(entry * (1 - min_dist))
    return min(stop_price, stop_floor)


def _portfolio(s: dict) -> Portfolio:
    # 사이징용 — 보유는 진입원가로 평가(mark-to-cost). 근사지만 섀도 사이징엔 충분.
    positions = {sym: Position(p["qty"], p["qty"] * p["entry_price"])
                 for sym, p in s["positions"].items()}
    holdings_val = sum(pos.value_krw for pos in positions.values())
    equity = s["cash_krw"] + holdings_val
    return Portfolio(equity, s["cash_krw"], positions)


def record_entries(date: str, entries, vol20_by_symbol: dict,
                   breadth: float = None, names: dict = None,
                   state: dict = None) -> list:
    """판정자 통과분(entries)을 섀도 포지션으로 진입 기록. 주문 없음.

    entries: [{symbol, entry_price, stop_price, target_price, horizon_days}, ...]
    breadth: 그날 시장 폭 (0~1). 폭 구간별 분석용으로 진입 이벤트에 남긴다.
    파이프라인과 동일 제약: 이미 보유 제외, 쿨다운 제외, 일 상한 MAX_NEW_PER_DAY,
    C2 손절 보정, position_size 사이징. 반환: 진입 이벤트 목록.

    폭 차단 여부와 무관하게 판정자 통과분을 다 기록한다 — 실제 데스크는 필터에
    막혀 거의 안 사므로, 섀도북이 곧 "필터 없었으면 어땠을지" 활성 계좌가 된다.
    """
    s = state if state is not None else load_state()
    new_today, events = 0, []
    for d in entries:
        sym = d["symbol"]
        if sym in s["positions"]:
            continue                                    # 이미 보유
        cd = s["cooldowns"].get(sym)
        if cd is not None and date < cd:
            continue                                    # 손절 쿨다운 중
        if new_today >= MAX_NEW_PER_DAY:
            break                                       # 일 신규 상한
        entry = round_down_to_tick(int(d["entry_price"]))
        stop = adjusted_stop(entry, int(d["stop_price"]),
                             float(vol20_by_symbol.get(sym, 0.0)))
        qty = position_size(entry, stop, _portfolio(s))
        if qty <= 0:
            continue                                    # 사이징 0 (리스크/현금 한도)
        name = (names or {}).get(sym, sym)
        cost = qty * entry
        s["cash_krw"] -= cost + _ceil_ppm(cost, COMMISSION_PPM)
        s["positions"][sym] = {
            "name": name, "qty": qty, "entry_price": entry, "stop": stop,
            "target": int(d["target_price"]), "horizon_days": int(d["horizon_days"]),
            "entry_date": date,
        }
        rec = {"event": "entry", "date": date, "symbol": sym, "name": name,
               "qty": qty, "entry_price": entry, "stop": stop,
               "target": int(d["target_price"]),
               "horizon_days": int(d["horizon_days"]), "breadth": breadth}
        events.append(rec)
        _append_trade(rec)
        new_today += 1
    s["last_date"] = date
    if state is None:
        save_state(s)
    return events


def resolve(bars_by_symbol: dict, state: dict = None) -> list:
    """보유 섀도를 미래 일봉으로 청산 판정 — 백테스트와 동일(손절→목표→기간).

    bars_by_symbol: {symbol: [{date, open, high, low, volume}, ...]} — 진입일 이후 봉.
    반환: 청산 이벤트 목록.
    """
    s = state if state is not None else load_state()
    closed = []
    for sym in list(s["positions"]):
        p = s["positions"][sym]
        bars = [b for b in bars_by_symbol.get(sym, []) if b["date"] > p["entry_date"]]
        for held, bar in enumerate(bars, start=1):
            kind, fill = "stop", fill_stop_sell(
                sym, p["stop"], p["qty"], bar["open"], bar["low"], bar["volume"])
            if fill is None:
                kind, fill = "target", fill_limit_sell(
                    sym, p["target"], p["qty"], bar["open"], bar["high"], bar["volume"])
            if fill is None and held >= p["horizon_days"]:
                kind, fill = "time", fill_market_sell(
                    sym, p["qty"], bar["open"], bar["volume"])
            if fill is None:
                continue
            s["cash_krw"] += fill.net_krw
            cost = p["qty"] * p["entry_price"]
            realized = fill.net_krw - (cost + _ceil_ppm(cost, COMMISSION_PPM))
            rec = {"event": "exit", "date": bar["date"], "symbol": sym,
                   "name": p.get("name", sym),
                   "reason": kind, "exit_price": fill.price,
                   "entry_date": p["entry_date"], "entry_price": p["entry_price"],
                   "qty": p["qty"], "hold_days": held,
                   "realized_krw": realized,
                   "ret_pct": round((fill.price / p["entry_price"] - 1) * 100, 2)}
            closed.append(rec)
            _append_trade(rec)
            if kind == "stop":
                s["cooldowns"][sym] = _add_trading_days(bar["date"], COOLDOWN_BARS,
                                                        bars_by_symbol.get(sym, []))
            del s["positions"][sym]
            break
    if state is None:
        save_state(s)
    return closed


def _add_trading_days(date: str, n: int, bars: list) -> str:
    """date 이후 n 거래일 뒤 날짜 (쿨다운 만료). 봉이 부족하면 date 유지(보수적)."""
    future = [b["date"] for b in bars if b["date"] > date]
    return future[n - 1] if len(future) >= n else (future[-1] if future else date)
