"""컴플라이언스 게이트. 절대규칙 2: 100% 결정적. LLM 호출 금지.

절대규칙 5 강제: 주문은 KIS '모의투자' 도메인 + 모의 TR ID 화이트리스트만 허용.
실전 도메인/실전 TR ID는 무조건 거부 — 이 상수를 바꾸지 않는 한 실거래 불가능.

추가 검사: KRX 6자리 종목코드 형식, 공매도 금지(보유 초과 매도 거부).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from autotrader.gates.types import OrderIntent, Portfolio
from autotrader.types import GateDecision

# KIS 모의투자 전용 (실전: openapi.koreainvestment.com:9443 — 사용 금지)
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
PAPER_TR_IDS = {
    "buy": "VTTC0802U",   # 국내주식 현금 매수 (모의)
    "sell": "VTTC0801U",  # 국내주식 현금 매도 (모의)
}

_SYMBOL_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class OrderEndpoint:
    base_url: str
    tr_id: str


def check_compliance(
    intent: OrderIntent, endpoint: OrderEndpoint, pf: Portfolio
) -> GateDecision:
    if endpoint.base_url != PAPER_BASE_URL:
        return GateDecision(
            False,
            f"절대규칙 5 위반: 모의투자 도메인 아님: {endpoint.base_url!r}",
        )
    expected_tr = PAPER_TR_IDS.get(intent.side)
    if expected_tr is None:
        return GateDecision(False, f"invalid side: {intent.side!r}")
    if endpoint.tr_id != expected_tr:
        return GateDecision(
            False,
            f"절대규칙 5 위반: 모의 TR ID 아님: {endpoint.tr_id!r} "
            f"(기대값 {expected_tr})",
        )
    if not _SYMBOL_RE.match(intent.symbol):
        return GateDecision(False, f"invalid symbol: {intent.symbol!r}")

    if intent.side == "sell":
        pos = pf.positions.get(intent.symbol)
        held = pos.qty if pos else 0
        if intent.qty > held:
            return GateDecision(
                False, f"공매도 금지: sell {intent.qty} > held {held}"
            )

    return GateDecision(True, "ok")
