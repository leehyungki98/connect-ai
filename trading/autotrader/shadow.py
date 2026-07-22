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
                                        fill_limit_buy, fill_limit_sell,
                                        fill_market_sell, fill_stop_sell)
from autotrader.execution.ticks import round_down_to_tick
from autotrader.gates.types import Portfolio, Position
from autotrader.sizing import position_size

_BASE = Path(__file__).resolve().parents[1]
STATE_FILE = _BASE / "state" / "shadow_state.json"          # 운영 데이터 (커밋 제외)
NAMES_FILE = _BASE / "state" / "symbol_names.json"          # 코드→종목명 캐시
LEDGER_DIR = _BASE / "ledger" / "shadow"                    # 학습 자산 (git 추적)
TRADES_FILE = LEDGER_DIR / "shadow_trades.jsonl"            # 계좌: 진입·청산 이력
SAMPLES_FILE = LEDGER_DIR / "shadow_samples.jsonl"          # 샘플: 제약 없이 전부
BREADTH_FILE = LEDGER_DIR / "breadth_log.jsonl"             # 폭 일지 (매일 한 줄)
RANKING_FILE = LEDGER_DIR / "ranking_log.jsonl"             # 그날 상위 랭킹 전체
WIDE_FILE = LEDGER_DIR / "wide_log.jsonl"                   # 확장 선정 결과 (0건 사유)


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
# 섀도 계좌의 일별 신규 진입 상한. **실계좌(pipeline.MAX_NEW_PER_DAY=2)와 일부러 다르다.**
# 가짜 돈이라 더 담아서 관찰하려고 사용자가 5로 정했다(2026-07-22).
# 이 값이 실계좌 주문 경로로 새면 안 된다 — shadow.py 는 주문을 내지 않으므로 구조적으로 불가.
MAX_NEW_PER_DAY = 5
STOP_VOL_K = 2.5              # C2 손절 거리 하한 배수
COOLDOWN_BARS = 10           # C2 손절 후 재진입 금지 거래일


def _fresh_state() -> dict:
    return {"cash_krw": INITIAL_CAPITAL_KRW, "positions": {}, "pending": [],
            "cooldowns": {}, "last_date": None}


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


def log_breadth(date: str, breadth: float, path: Path = None) -> None:
    """폭 일지 — 매일 한 줄. 지금은 기록만 하고 분석은 나중에 켠다.

    폭은 이미 매일 계산되지만 차단 메시지 텍스트에만 묻혀 있어 데이터로 못 썼다.
    과거 폭(그날 유니버스 200종목의 20일선 위 비율)은 사후 재구성이 어렵다 —
    오늘 안 적으면 오늘 데이터는 영영 없다. 같은 날 재실행은 마지막 값으로 교체.
    """
    if breadth is None:
        return
    p = path or BREADTH_FILE
    rows = []
    if p.exists():
        try:
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                    if x.strip() and json.loads(x).get("date") != date]
        except (OSError, ValueError):
            rows = []
    # source: 결정 시점 값인지 사후 백필인지 구분 — 폭 구간 분석의 기준값이라
    # 버킷 경계에서 같은 날이 다른 칸에 들어가면 안 된다. (2026-07-21 실제 사고)
    rows.append({"date": date, "breadth": round(float(breadth), 4),
                 "source": "premarket"})
    rows.sort(key=lambda r: r["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def log_ranking(date: str, rows: list, breadth: float = None,
                path: Path = None) -> None:
    """상위 랭킹 전체를 매일 남긴다 — 지금은 10개 중 2개만 남기고 8개를 버리고 있다.

    선정자가 안 고른 종목엔 손절/목표가가 없어 섀도 매매로는 못 돌린다. 대신
    '그 뒤 실제로 올랐나'는 나중에 일봉으로 언제든 붙일 수 있다 — 단 '그날 몇 등이었나'는
    사후 재구성이 안 된다(유니버스·지표가 그날 것이라). 그래서 순위만이라도 오늘 적어둔다.
    rows: [{symbol, name, rank, close, ret20, ret60, vol20, proposed}]
    """
    p = path or RANKING_FILE
    keep = []
    if p.exists():
        try:
            keep = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                    if x.strip() and json.loads(x).get("date") != date]
        except (OSError, ValueError):
            keep = []
    keep.append({"date": date, "breadth": breadth, "rows": rows})
    keep.sort(key=lambda r: r["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def log_wide(date: str, info: dict, path: Path = None) -> None:
    """확장 선정 결과를 매일 한 줄 — 0건의 이유를 구분하려고.

    확장이 0건인 이유는 둘이다: 선정자가 이미 다 뽑아서 더할 게 없거나, 호출이 터졌거나.
    이걸 안 남기면 몇 주 뒤 '확장 표본이 왜 없지'를 되짚을 방법이 없다.
    같은 날 재실행은 마지막 값으로 교체.
    """
    p = path or WIDE_FILE
    rows = []
    if p.exists():
        try:
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                    if x.strip() and json.loads(x).get("date") != date]
        except (OSError, ValueError):
            rows = []
    rows.append({"date": date, **info})
    rows.sort(key=lambda r: r["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def record_samples(date: str, entries, vol20_by_symbol: dict,
                   breadth: float = None, names: dict = None,
                   path: Path = None, origin: str = "선정자") -> list:
    """샘플 — 계좌 제약(현금·일2종목·보유중) **무시하고 판정자 통과분을 전부** 기록.

    왜: 계좌는 3~4일이면 꽉 차 그 뒤 몇 주간 폭이 샘플링되지 않는다. 게다가
    '돈이 남아있던 날'만 남아 편향된다. 가짜 돈이니 제약 없이 매일 다 남겨
    폭 구간별 표본을 빨리 쌓는다.

    ⚠ 합산 수익률을 '이만큼 벌었다'로 읽으면 안 된다 — 동시에 다 살 수 없었다.
       개별 트레이드 통계(승률·중앙값·실패분류)를 폭 구간별로 보는 용도다.
    사이징은 '갓 만든 1,000만원 계좌에서 이 트레이드만' 기준 (일관성).
    """
    p = path or SAMPLES_FILE
    fresh = Portfolio(INITIAL_CAPITAL_KRW, INITIAL_CAPITAL_KRW, {})
    out = []
    for d in entries:
        sym = d["symbol"]
        entry = round_down_to_tick(int(d["entry_price"]))
        stop = adjusted_stop(entry, int(d["stop_price"]),
                             float(vol20_by_symbol.get(sym, 0.0)))
        qty = position_size(entry, stop, fresh)
        if qty <= 0:
            continue
        out.append({
            "event": "sample_order", "date": date, "symbol": sym,
            "name": (names or {}).get(sym, sym), "qty": qty,
            "limit_price": entry, "stop": stop, "target": int(d["target_price"]),
            "horizon_days": int(d["horizon_days"]), "breadth": breadth,
            # rank: 그날 모멘텀 순위. origin: 실계좌가 실제로 쓴 선정("선정자")인지
            # 섀도 전용으로 더 받아온 확장분("확장")인지. 이 둘을 구분 없이 평균 내면
            # "3~5등까지 사도 되나"라는 질문 자체에 답할 수 없게 된다.
            "rank": d.get("rank"), "origin": origin,
            "status": "pending",
        })
    if out:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def adjusted_stop(entry: int, stop_price: int, vol20: float) -> int:
    """C2 손절 하한 보정 — 파이프라인과 동일: min(LLM손절, entry*(1-clamp(2.5σ,2%,10%)))."""
    min_dist = min(max(STOP_VOL_K * vol20, MIN_STOP_DIST), MAX_STOP_DIST)
    stop_floor = int(entry * (1 - min_dist))
    return min(stop_price, stop_floor)


def _portfolio(s: dict) -> Portfolio:
    """사이징용. 보유는 진입원가 평가(mark-to-cost), 미체결 주문은 현금을 예약 처리
    — 실전에서 같은 날 주문이 현금을 잠그는 것과 같다."""
    positions = {sym: Position(p["qty"], p["qty"] * p["entry_price"])
                 for sym, p in s["positions"].items()}
    holdings_val = sum(pos.value_krw for pos in positions.values())
    reserved = sum(o["qty"] * o["limit_price"] for o in s.get("pending", []))
    cash = max(0, s["cash_krw"] - reserved)
    return Portfolio(cash + holdings_val + reserved, cash, positions)


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
        if sym in s["positions"] or any(o["symbol"] == sym
                                        for o in s.get("pending", [])):
            continue                                    # 이미 보유 또는 주문 대기
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
        # 실전과 동일: 지정가 '주문 예약'일 뿐 아직 체결 아니다. 장중 저가가 지정가에
        # 닿아야 체결(settle_pending). 무조건 체결로 치면 낙관 편향이 생긴다.
        name = (names or {}).get(sym, sym)
        order = {"symbol": sym, "name": name, "qty": qty, "limit_price": entry,
                 "stop": stop, "target": int(d["target_price"]),
                 "horizon_days": int(d["horizon_days"]),
                 "order_date": date, "breadth": breadth}
        s.setdefault("pending", []).append(order)
        rec = {"event": "order", "date": date, "symbol": sym, "name": name,
               "qty": qty, "limit_price": entry, "stop": stop,
               "target": int(d["target_price"]),
               "horizon_days": int(d["horizon_days"]), "breadth": breadth}
        events.append(rec)
        _append_trade(rec)
        new_today += 1
    s["last_date"] = date
    if state is None:
        save_state(s)
    return events


def settle_pending(bars_by_symbol: dict, state: dict = None) -> list:
    """주문일 봉으로 지정가 매수 체결/미체결 판정 — 실전과 동일(저가 ≤ 지정가).

    체결되면 포지션 개설(실제 체결가·수수료 반영), 안 되면 '미체결'로 기록하고 취소.
    주문일 봉이 아직 없으면(장중) 그대로 대기. 반환: 체결·미체결 이벤트 목록.
    """
    s = state if state is not None else load_state()
    events, still_pending = [], []
    for o in s.get("pending", []):
        sym, od = o["symbol"], o["order_date"]
        bar = next((b for b in bars_by_symbol.get(sym, []) if b["date"] == od), None)
        if bar is None:
            still_pending.append(o)          # 주문일 봉 미확정 — 계속 대기
            continue
        fill = fill_limit_buy(sym, o["limit_price"], o["qty"],
                              bar["open"], bar["low"], bar["volume"])
        if fill is None:
            rec = {"event": "unfilled", "date": od, "symbol": sym,
                   "name": o.get("name", sym), "limit_price": o["limit_price"],
                   "day_low": bar["low"], "breadth": o.get("breadth"),
                   "failure_kind": "미체결"}
            events.append(rec)
            _append_trade(rec)
            continue                          # 취소 — 실전에서도 안 사진 것
        s["cash_krw"] += fill.net_krw          # 매수 net_krw 는 음수(대금+수수료)
        s["positions"][sym] = {
            "name": o.get("name", sym), "qty": fill.qty, "entry_price": fill.price,
            "stop": o["stop"], "target": o["target"],
            "horizon_days": o["horizon_days"], "entry_date": od,
            "breadth": o.get("breadth"),
        }
        rec = {"event": "entry", "date": od, "symbol": sym,
               "name": o.get("name", sym), "qty": fill.qty,
               "entry_price": fill.price, "limit_price": o["limit_price"],
               "stop": o["stop"], "target": o["target"],
               "horizon_days": o["horizon_days"], "breadth": o.get("breadth")}
        events.append(rec)
        _append_trade(rec)
    s["pending"] = still_pending
    if state is None:
        save_state(s)
    return events


def load_samples(path: Path = None) -> list:
    p = path or SAMPLES_FILE
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def _save_samples(rows: list, path: Path = None) -> None:
    p = path or SAMPLES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def resolve_samples(bars_by_symbol: dict, path: Path = None) -> dict:
    """샘플을 계좌와 **동일 로직**으로 체결·청산 판정 (제약만 없을 뿐 잣대는 같다).

    pending → 주문일 봉으로 지정가 체결 판정 → filled / unfilled
    filled  → 손절 → 목표 → 기간 순 청산, 실패 원인까지 분류 → closed
    반환: {"filled": n, "unfilled": n, "closed": n}
    """
    rows = load_samples(path)
    stat = {"filled": 0, "unfilled": 0, "closed": 0}
    for r in rows:
        bars = bars_by_symbol.get(r["symbol"], [])
        if r.get("status") == "pending":
            bar = next((b for b in bars if b["date"] == r["date"]), None)
            if bar is None:
                continue                       # 주문일 봉 미확정 — 대기
            fill = fill_limit_buy(r["symbol"], r["limit_price"], r["qty"],
                                  bar["open"], bar["low"], bar["volume"])
            if fill is None:
                r.update(status="unfilled", failure_kind="미체결",
                         day_low=bar["low"])
                stat["unfilled"] += 1
                continue
            r.update(status="filled", entry_price=fill.price,
                     entry_date=r["date"], qty=fill.qty)
            stat["filled"] += 1
        if r.get("status") != "filled":
            continue
        # 청산 판정 — 진입일 이후 봉
        after = [b for b in bars if b["date"] > r["entry_date"]]
        for held, bar in enumerate(after, start=1):
            kind, fill = "stop", fill_stop_sell(
                r["symbol"], r["stop"], r["qty"], bar["open"], bar["low"], bar["volume"])
            if fill is None:
                kind, fill = "target", fill_limit_sell(
                    r["symbol"], r["target"], r["qty"], bar["open"], bar["high"], bar["volume"])
            if fill is None and held >= r["horizon_days"]:
                kind, fill = "time", fill_market_sell(
                    r["symbol"], r["qty"], bar["open"], bar["volume"])
            if fill is None:
                continue
            cost = r["qty"] * r["entry_price"]
            r.update(status="closed", exit_date=bar["date"], exit_price=fill.price,
                     reason=kind, hold_days=held,
                     failure_kind=_failure_kind(kind, bar, r, held),
                     realized_krw=fill.net_krw - (cost + _ceil_ppm(cost, COMMISSION_PPM)),
                     ret_pct=round((fill.price / r["entry_price"] - 1) * 100, 2))
            stat["closed"] += 1
            break
    _save_samples(rows, path)
    return stat


def _failure_kind(reason: str, bar: dict, p: dict, held: int) -> str:
    """청산 원인 분류 — 백데이터 분석용 (왜 실패했나)."""
    if reason == "target":
        return "목표달성"
    if reason == "time":
        return "기간만료"
    if bar["open"] <= p["stop"]:
        return "갭손절"      # 시가가 이미 손절 밑 — 손절이 못 지킴(오버나잇 리스크)
    return "1봉손절" if held <= 1 else "손절"


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
                   "reason": kind, "failure_kind": _failure_kind(kind, bar, p, held),
                   "breadth": p.get("breadth"), "exit_price": fill.price,
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
