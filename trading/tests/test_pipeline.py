"""파이프라인 검증: 킬스위치/한도 차단, 청산·진입 전체 흐름, C2 채택 파라미터."""
import json
from datetime import date

from autotrader.execution.holdings import HoldingMeta, HoldingsStore
from autotrader.gates.types import Portfolio, Position
from autotrader.kis.client import OrderResult
from autotrader.pipeline import run_premarket
from autotrader.safety.guard import SafetyGuard
from autotrader.safety.killswitch import KillSwitch
from autotrader.screener.ranking import SymbolData

TODAY = date(2026, 7, 20)


class FakeKIS:
    def __init__(self, pf, prices):
        self.pf = pf
        self.prices = prices
        self.orders = []

    def get_portfolio(self):
        return self.pf

    def get_price(self, symbol):
        return self.prices[symbol]

    def place_order(self, intent, pf):
        self.orders.append(intent)
        return OrderResult(True, "0001", "ok")


def uptrend_universe(symbols=("005930", "000660"), n_fill=40):
    """상승 후보 + 완만 상승 필러 (시장 폭 필터 통과: 전 종목 이평 위)."""
    out = []
    for i, sym in enumerate(symbols):
        start = 10_000 + i * 1_000
        closes = tuple(round(start * 1.004**j) for j in range(90))
        out.append(SymbolData(sym, closes))
    for i in range(n_fill):
        out.append(SymbolData(f"90{i:04d}",
                              tuple(round(5_000 * 1.002**j) for j in range(90))))
    return out


def flat_universe(symbols=("005930",), n_fill=40):
    """후보 소수 + 횡보 필러 다수 → 시장 폭 < 50% (C2 필터 발동)."""
    out = []
    for i, sym in enumerate(symbols):
        start = 10_000 + i * 1_000
        out.append(SymbolData(sym, tuple(round(start * 1.004**j) for j in range(90))))
    for i in range(n_fill):
        out.append(SymbolData(f"90{i:04d}", tuple([5_000] * 90)))
    return out


def enter_brain(symbol, entry, stop, target):
    payload = json.dumps({
        "decisions": [{"symbol": symbol, "action": "enter", "entry_price": entry,
                       "stop_price": stop, "target_price": target,
                       "horizon_days": 10, "reason": "test"}],
        "exits": [],
    })
    return lambda brain, prompt: payload


NO_ACTION = lambda brain, prompt: '{"decisions": [], "exits": []}'


def judge_pass_all(brain, prompt):
    import re
    m = re.search(r"판정 대상: ([0-9, ]+)", prompt)
    syms = [s for s in m.group(1).replace(" ", "").split(",") if s]
    return json.dumps({"verdicts": [
        {"symbol": s, "verdict": "pass", "criterion": None, "reason": "기준 미해당"}
        for s in syms
    ]})


def setup(tmp_path, pf, prices):
    ks = KillSwitch(tmp_path / "ks.json")
    guard = SafetyGuard(ks, tmp_path / "block.json")
    store = HoldingsStore(tmp_path / "holdings.json")
    kis = FakeKIS(pf, prices)
    return ks, guard, store, kis


def run(kis, guard, store, tmp_path, runner, universe=None, judge=judge_pass_all):
    return run_premarket(
        kis, guard, store, universe or uptrend_universe(), TODAY,
        tmp_path / "day_start.json", tmp_path / "cooldowns.json",
        proposer_runner=runner, judge_runner=judge,
    )


# --- 차단 경로 ---

def test_killswitch_blocks_all_orders(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    ks.engage("수동 정지")
    r = run(kis, guard, store, tmp_path, enter_brain("005930", 14_000, 13_000, 16_000))
    assert "killswitch" in r.blocked and kis.orders == []


def test_daily_loss_blocks_all_orders(tmp_path):
    pf_start = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf_start, {})
    (tmp_path / "day_start.json").write_text(
        json.dumps({"date": TODAY.isoformat(), "equity": 10_000_000})
    )
    kis.pf = Portfolio(9_600_000, 9_600_000, {})
    r = run(kis, guard, store, tmp_path, enter_brain("005930", 14_000, 13_000, 16_000))
    assert "daily loss" in r.blocked and kis.orders == []


def test_corrupted_holdings_blocks(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    (tmp_path / "holdings.json").write_text("{broken", encoding="utf-8")
    r = run(kis, guard, store, tmp_path, NO_ACTION)
    assert "fail-closed" in r.blocked and kis.orders == []


def test_corrupted_day_start_blocks(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    (tmp_path / "day_start.json").write_text("garbage", encoding="utf-8")
    r = run(kis, guard, store, tmp_path, NO_ACTION)
    assert "day_start" in r.blocked and kis.orders == []


# --- 청산 경로 ---

def test_stop_hit_places_sell(tmp_path):
    pf = Portfolio(10_000_000, 9_000_000, {"005930": Position(10, 650_000)})
    ks, guard, store, kis = setup(tmp_path, pf, {"005930": 65_000})
    store.save({"005930": HoldingMeta(66_000, 80_000, 10, "2026-07-15")})
    r = run(kis, guard, store, tmp_path, NO_ACTION)
    assert len(r.exits_placed) == 1 and "(stop)" in r.exits_placed[0]
    sell = kis.orders[0]
    assert sell.side == "sell" and sell.qty == 10
    assert sell.limit_price == 63_700  # 65000*0.98 (tick OK)


def test_brain_exit_places_sell(tmp_path):
    pf = Portfolio(10_000_000, 9_000_000, {"005930": Position(10, 700_000)})
    ks, guard, store, kis = setup(tmp_path, pf, {"005930": 70_000})
    store.save({"005930": HoldingMeta(66_000, 80_000, 30, "2026-07-15")})
    runner = lambda b, p: json.dumps(
        {"decisions": [], "exits": [{"symbol": "005930", "reason": "약세"}]}
    )
    r = run(kis, guard, store, tmp_path, runner)
    assert len(r.exits_placed) == 1 and "(brain)" in r.exits_placed[0]


# --- 진입 경로 ---

def test_brain_enter_places_buy_and_saves_meta(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    r = run(kis, guard, store, tmp_path, enter_brain("005930", 14_000, 13_000, 16_000))
    assert r.brain_ok is True and len(r.buys_placed) == 1
    buy = kis.orders[0]
    assert buy.side == "buy" and buy.symbol == "005930"
    # qty = min(150000//1000=150, 1500000//14000=107, 10000000//14000=714) = 107
    assert buy.qty == 107
    meta = store.load()["005930"]
    assert meta.stop_price == 13_000 and meta.entry_date == TODAY.isoformat()


def test_invalid_brain_no_buys_but_exits_still_run(tmp_path):
    pf = Portfolio(10_000_000, 9_000_000, {"005930": Position(10, 650_000)})
    ks, guard, store, kis = setup(tmp_path, pf, {"005930": 65_000})
    store.save({"005930": HoldingMeta(66_000, 80_000, 10, "2026-07-15")})
    r = run(kis, guard, store, tmp_path, lambda b, p: "완전히 깨진 출력")
    assert r.brain_ok is False and r.buys_placed == []
    assert len(r.exits_placed) == 1  # 결정적 청산은 브레인과 무관하게 실행


def test_cash_tracked_across_buys_in_one_run(tmp_path):
    """두 종목 진입 시 첫 매수 비용이 차감돼 둘째 사이징이 축소되는지."""
    pf = Portfolio(10_000_000, 2_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    payload = json.dumps({
        "decisions": [
            {"symbol": "005930", "action": "enter", "entry_price": 14_000,
             "stop_price": 13_000, "target_price": 16_000, "horizon_days": 10,
             "reason": "a"},
            {"symbol": "000660", "action": "enter", "entry_price": 15_000,
             "stop_price": 14_000, "target_price": 17_000, "horizon_days": 10,
             "reason": "b"},
        ],
        "exits": [],
    })
    r = run(kis, guard, store, tmp_path, lambda b, p: payload)
    assert len(r.buys_placed) == 2
    assert kis.orders[0].qty == 107 and kis.orders[1].qty == 33


def test_same_day_reentry_forbidden(tmp_path):
    pf = Portfolio(10_000_000, 9_000_000, {"005930": Position(10, 650_000)})
    ks, guard, store, kis = setup(tmp_path, pf, {"005930": 65_000})
    store.save({"005930": HoldingMeta(66_000, 80_000, 10, "2026-07-15")})
    r = run(kis, guard, store, tmp_path, enter_brain("005930", 14_000, 13_000, 16_000))
    assert len(r.exits_placed) == 1 and r.buys_placed == []
    assert any("재진입 금지" in s for s in r.skipped)


def test_judge_rejection_blocks_buy(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    judge_reject = lambda b, p: json.dumps({"verdicts": [
        {"symbol": "005930", "verdict": "reject", "criterion": "K3",
         "reason": "근거가 데이터와 모순"}]})
    r = run(kis, guard, store, tmp_path,
            enter_brain("005930", 14_000, 13_000, 16_000), judge=judge_reject)
    assert r.buys_placed == [] and kis.orders == []
    assert any("판정 기각(K3)" in x for x in r.skipped)


# --- C2 채택 파라미터 (2026-07-19 승인) ---

def test_c2_market_breadth_blocks_new_entries(tmp_path):
    """시장 폭 < 50% → 신규 진입 전면 중단, 청산은 계속."""
    pf = Portfolio(10_000_000, 9_000_000, {"000660": Position(5, 500_000)})
    ks, guard, store, kis = setup(tmp_path, pf, {"000660": 90_000})
    store.save({"000660": HoldingMeta(95_000, 120_000, 10, "2026-07-15")})  # stop 이탈
    r = run(kis, guard, store, tmp_path,
            enter_brain("005930", 14_000, 13_000, 16_000),
            universe=flat_universe())
    assert r.buys_placed == []
    assert any("시장 폭" in s for s in r.skipped)
    assert len(r.exits_placed) == 1  # 청산은 필터와 무관


def test_c2_stop_cooldown_blocks_reentry_next_run(tmp_path):
    """손절 매도 → 쿨다운 기록 → 다음 실행에서 같은 종목 진입 차단."""
    pf1 = Portfolio(10_000_000, 9_000_000, {"005930": Position(10, 650_000)})
    ks, guard, store, kis1 = setup(tmp_path, pf1, {"005930": 65_000})
    store.save({"005930": HoldingMeta(66_000, 80_000, 10, "2026-07-15")})
    r1 = run(kis1, guard, store, tmp_path, NO_ACTION)
    assert "(stop)" in r1.exits_placed[0]

    kis2 = FakeKIS(Portfolio(10_000_000, 10_000_000, {}), {})
    r2 = run(kis2, guard, store, tmp_path,
             enter_brain("005930", 14_000, 13_000, 16_000))
    assert r2.buys_placed == [] and kis2.orders == []
    assert any("쿨다운" in s for s in r2.skipped)


def test_c2_stop_floor_widens_tight_llm_stop(tmp_path):
    """LLM 손절이 2.5σ 하한(여기선 클램프 2%)보다 좁으면 넓혀서 저장."""
    pf = Portfolio(10_000_000, 10_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    # stop 13,860 = 1% 거리 < 하한 2% → 13,720(=14000*0.98)으로 보정
    r = run(kis, guard, store, tmp_path, enter_brain("005930", 14_000, 13_860, 16_000))
    assert len(r.buys_placed) == 1
    assert store.load()["005930"].stop_price == 13_720
    assert any("손절 보정" in s for s in r.skipped)


def test_c2_max_new_per_day_cap(tmp_path):
    """진입 승인 3건이어도 하루 2건까지만 주문."""
    pf = Portfolio(30_000_000, 30_000_000, {})
    ks, guard, store, kis = setup(tmp_path, pf, {})
    payload = json.dumps({
        "decisions": [
            {"symbol": "005930", "action": "enter", "entry_price": 14_000,
             "stop_price": 13_000, "target_price": 16_000, "horizon_days": 10,
             "reason": "a"},
            {"symbol": "000660", "action": "enter", "entry_price": 15_500,
             "stop_price": 14_500, "target_price": 17_500, "horizon_days": 10,
             "reason": "b"},
            {"symbol": "035420", "action": "enter", "entry_price": 17_000,
             "stop_price": 16_000, "target_price": 19_000, "horizon_days": 10,
             "reason": "c"},
        ],
        "exits": [],
    })
    r = run(kis, guard, store, tmp_path, lambda b, p: payload,
            universe=uptrend_universe(symbols=("005930", "000660", "035420")))
    assert len(r.buys_placed) == 2
    assert any("상한" in s for s in r.skipped)
