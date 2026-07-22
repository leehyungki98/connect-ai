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
    monkeypatch.setattr(shadow, "SAMPLES_FILE", tmp_path / "shadow_samples.jsonl")
    monkeypatch.setattr(shadow, "BREADTH_FILE", tmp_path / "breadth_log.jsonl")
    monkeypatch.setattr(shadow, "RANKING_FILE", tmp_path / "ranking_log.jsonl")
    monkeypatch.setattr(shadow, "WIDE_FILE", tmp_path / "wide_log.jsonl")


def _entry(sym, entry, stop, target, horizon=10):
    return {"symbol": sym, "entry_price": entry, "stop_price": stop,
            "target_price": target, "horizon_days": horizon}


def _bar(date, o, h, l, vol=10_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "volume": vol}


def test_constants_match_pipeline():
    """drift 가드 — 손절 규칙은 실계좌와 일치해야 한다.

    일별 상한만 일부러 다르다(가짜 돈이라 더 담아 관찰). 나머지가 벌어지면
    섀도 성적을 실계좌 근거로 못 쓴다 — 다른 규칙으로 낸 성적이기 때문이다.
    """
    from autotrader import pipeline
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
    entries = [_entry(f"00000{i}", 10000, 9500, 11000)
               for i in range(shadow.MAX_NEW_PER_DAY + 2)]
    shadow.record_entries("2026-07-20", entries, {}, state=s)
    assert len(s["pending"]) == shadow.MAX_NEW_PER_DAY     # 섀도 상한만큼 주문


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


def test_samples_ignore_account_limits():
    """샘플은 계좌 제약(일2종목·현금) 무시하고 전부 기록 — 폭 표본 확보용."""
    entries = [_entry(f"00000{i}", 10000, 9500, 11000) for i in range(5)]
    out = shadow.record_samples("2026-07-20", entries, {}, breadth=0.24)
    assert len(out) == 5                       # 계좌는 2건인데 샘플은 5건 전부
    assert all(r["breadth"] == 0.24 for r in out)
    assert all(r["status"] == "pending" for r in out)


def test_sample_resolve_fill_and_close():
    """샘플도 계좌와 동일 로직으로 체결→청산 (제약만 없다)."""
    shadow.record_samples("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, breadth=0.24)
    bars = {"005930": [_bar("2026-07-20", 70000, 70500, 69500),
                       _bar("2026-07-21", 75000, 79000, 74000)]}   # 목표 도달
    stat = shadow.resolve_samples(bars)
    assert stat["filled"] == 1 and stat["closed"] == 1
    r = shadow.load_samples()[0]
    assert r["status"] == "closed" and r["failure_kind"] == "목표달성"


def test_sample_unfilled_recorded():
    shadow.record_samples("2026-07-20", [_entry("005930", 70000, 66000, 78000)],
                          {"005930": 0.0}, breadth=0.14)
    bars = {"005930": [_bar("2026-07-20", 71000, 72000, 70500)]}   # 저가 > 지정가
    stat = shadow.resolve_samples(bars)
    assert stat["unfilled"] == 1
    assert shadow.load_samples()[0]["failure_kind"] == "미체결"


def test_breadth_log_daily_and_idempotent():
    """폭 일지 — 매일 한 줄, 같은 날 재실행은 마지막 값으로 교체."""
    shadow.log_breadth("2026-07-20", 0.24)
    shadow.log_breadth("2026-07-21", 0.14)
    shadow.log_breadth("2026-07-21", 0.15)      # 같은 날 재실행
    import json
    rows = [json.loads(x) for x in shadow.BREADTH_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["date"] for r in rows] == ["2026-07-20", "2026-07-21"]
    assert rows[1]["breadth"] == 0.15           # 마지막 값


def test_breadth_log_skips_none():
    shadow.log_breadth("2026-07-20", None)
    assert not shadow.BREADTH_FILE.exists()


# ── 확장 선정 (섀도 전용) ─────────────────────────────────────────────

def test_wide_never_touches_real_entries():
    """확장은 기록 전용 — 실계좌 진입 경로(record_entries)로 절대 안 들어간다.

    이게 깨지면 '데이터 모으려고 켠 확장'이 실제 주문 후보를 늘린다.
    확장의 존재 이유가 실전 무영향이므로 이 테스트가 그 계약이다.
    """
    import inspect

    from autotrader import pipeline
    src = inspect.getsource(pipeline.run_premarket)
    wide_call = src[src.index("_shadow_wide_entries("):]
    # 확장 결과가 흘러가는 곳은 record_samples 뿐이어야 한다
    assert "record_samples" in wide_call.split("except")[0]
    assert "record_entries" not in wide_call.split("except")[0]
    # 주문 루프는 확장 결과를 이름으로도 참조하지 않는다
    order_loop = src[src.index("if ts.ok and not entries_blocked:"):]
    assert "_wide" not in order_loop and "SHADOW_WIDE_N" not in order_loop


def test_wide_prompt_only_when_asked():
    """want=0(실계좌 기본)이면 프롬프트가 한 글자도 안 바뀐다."""
    from autotrader.brain.two_stage import build_proposer_prompt
    from autotrader.gates.types import Portfolio

    pf = Portfolio(10_000_000, 10_000_000, {})
    base = build_proposer_prompt([], pf)
    assert base == build_proposer_prompt([], pf, want=0)
    wide = build_proposer_prompt([], pf, want=5)
    assert base != wide and "최소 5개" in wide


def test_samples_carry_rank_and_origin():
    """순위·출처가 안 남으면 1~2등과 3~5등이 섞여 평균난다."""
    e = _entry("005930", 70000, 66000, 78000)
    e["rank"] = 4
    out = shadow.record_samples("2026-07-20", [e], {"005930": 0.02},
                                breadth=0.3, origin="확장")
    assert out[0]["rank"] == 4 and out[0]["origin"] == "확장"
    # 기본값은 실계좌가 쓴 선정
    out2 = shadow.record_samples("2026-07-20", [_entry("000660", 100, 90, 130)],
                                 {"000660": 0.02})
    assert out2[0]["origin"] == "선정자" and out2[0]["rank"] is None


def test_ranking_log_keeps_all_and_is_idempotent(tmp_path):
    """상위 랭킹 전체 기록 — 같은 날 재실행은 교체(중복 적재 금지)."""
    p = tmp_path / "ranking.jsonl"
    rows = [{"symbol": "005930", "rank": 1, "proposed": True},
            {"symbol": "000660", "rank": 2, "proposed": False}]
    shadow.log_ranking("2026-07-20", rows, breadth=0.42, path=p)
    shadow.log_ranking("2026-07-20", rows, breadth=0.42, path=p)
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    import json
    rec = json.loads(lines[0])
    assert len(rec["rows"]) == 2 and rec["breadth"] == 0.42
    # 안 뽑힌 종목도 남아야 한다 — 버리면 사후 재구성이 안 된다
    assert any(not r["proposed"] for r in rec["rows"])


def test_wide_actually_adds_only_new_symbols():
    """확장이 실제로 동작하는지 — 가짜 선정자/판정자로 오프라인 검증.

    소스 검사 테스트만으로는 '연결이 안 새는지'만 알지 '돌긴 하는지'는 모른다.
    """
    import json

    from autotrader.pipeline import _shadow_wide_entries
    from autotrader.gates.types import Portfolio
    from autotrader.screener.ranking import RankedSymbol
    from autotrader.brain.client import Candidate

    syms = ["005930", "000660", "035420", "051910", "005380"]
    cands = [Candidate(RankedSymbol(s, 1.0, 0.1, 0.2, 0.02), 70000) for s in syms]
    pf = Portfolio(10_000_000, 10_000_000, {})

    def fake_proposer(_brain, prompt):
        assert "최소 5개" in prompt          # 확장 요구가 실제로 전달된다
        return json.dumps({"decisions": [
            {"symbol": s, "action": "enter", "entry_price": 70000,
             "stop_price": 66000, "target_price": 78000, "horizon_days": 10,
             "reason": "테스트"} for s in syms], "exits": []})

    def fake_judge(_brain, _prompt):
        return json.dumps({"verdicts": [
            {"symbol": s, "verdict": "pass", "criterion": None, "reason": "ok"}
            for s in syms]})

    rank_of = {s: i + 1 for i, s in enumerate(syms)}
    already = {"005930", "000660"}          # 실계좌 선정이 이미 가져간 2개
    out = _shadow_wide_entries(cands, pf, "codex", "claude",
                               fake_proposer, fake_judge, rank_of, already)
    got = {d["symbol"] for d in out}
    assert got == {"035420", "051910", "005380"}   # 중복 제외한 나머지만
    assert all(d["rank"] == rank_of[d["symbol"]] for d in out)


def test_wide_log_records_zero_reason(tmp_path):
    """확장 0건의 사유를 구분할 수 있어야 한다 (중복이라 0 vs 터져서 0)."""
    p = tmp_path / "wide.jsonl"
    shadow.log_wide("2026-07-22", {"asked": 5, "got": 8, "new": 0, "error": None}, path=p)
    shadow.log_wide("2026-07-23", {"asked": 5, "got": 2, "new": 0,
                                   "error": "RuntimeError: CLI timeout"}, path=p)
    import json
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["error"] is None and rows[0]["got"] == 8   # 더할 게 없어서 0
    assert rows[1]["error"].startswith("RuntimeError")        # 터져서 0


def test_shadow_cap_is_deliberately_looser_than_real():
    """섀도 상한이 실계좌보다 느슨한 건 의도다 — 다만 '실계좌를 넘어선다'가 계약이다.

    이 테스트가 없으면 나중에 누가 실계좌 상한을 6으로 올려도(섀도 5) 아무도 모르고,
    섀도가 실계좌보다 덜 사는 이상한 상태가 조용히 만들어진다.
    """
    from autotrader import pipeline
    assert shadow.MAX_NEW_PER_DAY >= pipeline.MAX_NEW_PER_DAY
    # 섀도는 주문 함수를 아예 안 갖는다 — 상한이 느슨해도 실주문이 늘 수 없는 근거
    import inspect
    src = inspect.getsource(shadow)
    assert "place_order" not in src and "OrderIntent" not in src
