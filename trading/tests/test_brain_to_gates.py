"""목표 5 검증: 브레인 출력 → 스키마 → 사이징 → 리스크·컴플라이언스 게이트 통과."""
import json

from autotrader.brain.client import Candidate, ask_brain
from autotrader.gates.compliance import PAPER_BASE_URL, OrderEndpoint, check_compliance
from autotrader.gates.risk import check_risk
from autotrader.gates.types import OrderIntent, Portfolio, Position
from autotrader.screener.ranking import RankedSymbol
from autotrader.sizing import position_size

CANDS = [Candidate(RankedSymbol("005930", 0.1, 0.08, 0.12, 0.02), 70_000)]
PF = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 600_000)})
BUY_EP = OrderEndpoint(PAPER_BASE_URL, "VTTC0802U")
SELL_EP = OrderEndpoint(PAPER_BASE_URL, "VTTC0801U")


def canned(payload):
    return lambda brain, prompt: json.dumps(payload)


def test_enter_decision_passes_both_gates():
    r = ask_brain(CANDS, PF, runner=canned({
        "decisions": [{"symbol": "005930", "action": "enter",
                       "entry_price": 70_000, "stop_price": 66_000,
                       "target_price": 78_000, "horizon_days": 10,
                       "reason": "추세 지속"}],
        "exits": [],
    }))
    assert r.ok is True
    d = r.output.decisions[0]

    qty = position_size(d.entry_price, d.stop_price, PF)
    assert qty == 21  # min(150000//4000=37, 1500000//70000=21, 8000000//70000=114)

    intent = OrderIntent("buy", d.symbol, qty, d.entry_price, d.stop_price)
    assert check_risk(intent, PF).allowed is True
    assert check_compliance(intent, BUY_EP, PF).allowed is True


def test_exit_decision_passes_both_gates():
    r = ask_brain(CANDS, PF, runner=canned({
        "decisions": [],
        "exits": [{"symbol": "035720", "reason": "모멘텀 소멸"}],
    }))
    assert r.ok is True
    e = r.output.exits[0]
    intent = OrderIntent("sell", e.symbol, PF.positions[e.symbol].qty, 120_000)
    assert check_risk(intent, PF).allowed is True
    assert check_compliance(intent, SELL_EP, PF).allowed is True


def test_invalid_brain_output_produces_no_orders():
    """절대규칙 3: 검증 실패 → 주문 0건, 이전 상태 유지."""
    r = ask_brain(CANDS, PF, runner=canned({
        "decisions": [{"symbol": "999999", "action": "enter",  # 후보에 없음
                       "entry_price": 70_000, "stop_price": 66_000,
                       "target_price": 78_000, "horizon_days": 10, "reason": "x"}],
        "exits": [],
    }))
    assert r.ok is False and r.output is None
    # output이 None이므로 주문을 만들 데이터 자체가 없다


def test_zero_size_means_no_order():
    """사이징이 0이면 주문 생성 금지 (게이트 이전에 차단)."""
    poor = Portfolio(10_000_000, 50_000, {})  # 현금 부족
    qty = position_size(70_000, 66_000, poor)
    assert qty == 0
