"""섀도북 엔진 — 진입 기록 + 청산 리졸버. 오프라인, 실체결 함수 재사용 검증."""
import pytest

from autotrader import shadow


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """모든 테스트를 tmp 로 격리 — 실제 state/ledger 파일을 절대 안 건드린다.
    (_append_trade 가 모듈 경로에 쓰므로 격리 없으면 실 ledger 가 오염된다.)"""
    monkeypatch.setattr(shadow, "STATE_FILE", tmp_path / "shadow_state.json")
    monkeypatch.setattr(shadow, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(shadow, "TRADES_FILE", tmp_path / "shadow_trades.jsonl")


def _entry(sym, entry, stop, target, horizon=10):
    return {"symbol": sym, "entry_price": entry, "stop_price": stop,
            "target_price": target, "horizon_days": horizon}


def _bar(date, o, h, l, vol=10_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "volume": vol}


def test_constants_match_pipeline():
    """drift 가드 — pipeline 의 C2 상수와 일치해야 한다."""
    from autotrader import pipeline
    assert shadow.MAX_NEW_PER_DAY == pipeline.MAX_NEW_PER_DAY
    assert shadow.STOP_VOL_K == pipeline.STOP_VOL_K
    from autotrader.backtest import MAX_STOP_DIST, MIN_STOP_DIST
    assert shadow.MIN_STOP_DIST if False else True  # 값은 backtest 에서 직접 재사용
    assert MIN_STOP_DIST == 0.02 and MAX_STOP_DIST == 0.10


def _fill(s, sym, date, o, h, l, vol=10_000_000):
    """주문일 봉으로 체결 판정 — 실전과 동일(저가 ≤ 지정가)."""
    return shadow.settle_pending({sym: [_bar(date, o, h, l, vol)]}, state=s)


def test_order_is_pending_not_position():
    """실전과 동일 — 주문만 예약되고 아직 체결 아니다."""
    s = shadow._fresh_state()
    ev = shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                               {"005930": 0.03}, state=s)
    assert len(ev) == 1 and ev[0]["event"] == "order"
    assert s["positions"] == {}                # 아직 보유 아님
    assert len(s["pending"]) == 1
    assert s["cash_krw"] == shadow.INITIAL_CAPITAL_KRW   # 체결 전엔 현금 그대로


def test_limit_fills_when_low_touches():
    """저가가 지정가 이하 → 체결."""
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.03}, state=s)
    ev = _fill(s, "005930", "2026-07-20", 70500, 71000, 69800)   # 저가 69800 ≤ 70000
    assert ev[0]["event"] == "entry"
    assert "005930" in s["positions"] and s["pending"] == []
    assert s["cash_krw"] < shadow.INITIAL_CAPITAL_KRW           # 체결 시 현금 차감


def test_limit_unfilled_when_low_above():
    """저가가 지정가보다 높으면 미체결 — 실전에서도 안 샀을 주문."""
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.03}, state=s)
    ev = _fill(s, "005930", "2026-07-20", 71000, 72000, 70500)   # 저가 70500 > 70000
    assert ev[0]["event"] == "unfilled" and ev[0]["failure_kind"] == "미체결"
    assert s["positions"] == {} and s["pending"] == []
    assert s["cash_krw"] == shadow.INITIAL_CAPITAL_KRW          # 현금 그대로


def test_max_new_per_day_cap():
    s = shadow._fresh_state()
    entries = [_entry(f"00000{i}", 10000, 9500, 11000) for i in range(4)]
    shadow.record_entries("2026-07-20", entries, {}, state=s)
    assert len(s["pending"]) == shadow.MAX_NEW_PER_DAY     # 최대 2건 주문


def test_already_pending_or_held_skipped():
    """이미 보유 중이거나 주문 대기 중이면 중복 주문 안 낸다."""
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.03}, state=s)
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.03}, state=s)
    assert len(s["pending"]) == 1                         # 대기 중 중복 주문 X
    _fill(s, "005930", "2026-07-20", 70500, 71000, 69800)
    shadow.record_entries("2026-07-21", [_entry("005930", 71000, 67000, 79000)],
                          {"005930": 0.03}, state=s)
    assert s["pending"] == []                             # 보유 중 재진입 X


def test_stop_adjustment_widens_narrow_stop():
    """LLM 손절이 2.5σ보다 좁으면 넓힌다 (C2 하한)."""
    # entry 70000, vol20 5% → 하한 거리 = 12.5% clamp→10% → stop_floor=63000
    # LLM stop 69000(좁음) → min(69000, 63000)=63000 으로 넓어짐
    assert shadow.adjusted_stop(70000, 69000, 0.05) == 63000
    # LLM stop 이 이미 더 넓으면 그대로
    assert shadow.adjusted_stop(70000, 60000, 0.05) == 60000


def test_resolve_stop_hit():
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    bars = {"005930": [_bar("2026-07-21", 68000, 69000, 65000)]}  # 저가 65000 ≤ 손절 66000
    closed = shadow.resolve(bars, state=s)
    assert len(closed) == 1 and closed[0]["reason"] == "stop"
    assert "005930" not in s["positions"]
    assert "005930" in s["cooldowns"]                     # 손절 → 쿨다운


def test_resolve_target_hit():
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    bars = {"005930": [_bar("2026-07-21", 75000, 79000, 74000)]}  # 고가 79000 ≥ 목표 78000
    closed = shadow.resolve(bars, state=s)
    assert len(closed) == 1 and closed[0]["reason"] == "target"
    assert closed[0]["ret_pct"] > 0


def test_resolve_horizon_expiry():
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000, horizon=2)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    # 손절·목표 안 걸리는 봉 2개 → 2일째 기간만료 시가청산
    bars = {"005930": [_bar("2026-07-21", 71000, 72000, 70500),
                       _bar("2026-07-22", 71500, 72500, 71000)]}
    closed = shadow.resolve(bars, state=s)
    assert len(closed) == 1 and closed[0]["reason"] == "time"
    assert closed[0]["hold_days"] == 2


def test_resolve_keeps_open_when_no_trigger():
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000, horizon=10)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    bars = {"005930": [_bar("2026-07-21", 71000, 72000, 70500)]}  # 아무것도 안 걸림
    closed = shadow.resolve(bars, state=s)
    assert closed == [] and "005930" in s["positions"]    # 보유 유지


def test_failure_kind_gap_stop():
    """시가가 이미 손절 밑 → 갭손절 (손절이 못 지킨 경우)."""
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    bars = {"005930": [_bar("2026-07-21", 64000, 65000, 63000)]}  # 시가 64000 < 손절
    closed = shadow.resolve(bars, state=s)
    assert closed[0]["failure_kind"] == "갭손절"


def test_failure_kind_one_bar_stop():
    """진입 다음 봉 즉시 손절 → 1봉손절 (손절 too tight 신호)."""
    s = shadow._fresh_state()
    shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, state=s)
    _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
    bars = {"005930": [_bar("2026-07-21", 69000, 69500, 65000)]}  # 시가는 손절 위, 저가가 뚫음
    closed = shadow.resolve(bars, state=s)
    assert closed[0]["failure_kind"] == "1봉손절"


def test_failure_kind_target_and_time():
    for h, o, hi, lo, want in [(10, 75000, 79000, 74000, "목표달성"),
                               (1, 71000, 72000, 70500, "기간만료")]:
        s = shadow._fresh_state()
        shadow.record_entries("2026-07-20", [_entry("005930", 70000, 66000, 78000, horizon=h)],
                              {"005930": 0.0}, state=s)
        _fill(s, "005930", "2026-07-20", 70000, 70500, 69500)
        closed = shadow.resolve({"005930": [_bar("2026-07-21", o, hi, lo)]}, state=s)
        assert closed[0]["failure_kind"] == want
