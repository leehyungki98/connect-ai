"""편입 기준 판정용 재무 지표 수집 (yfinance).

결측은 None 으로 남긴다 — 조용한 기본값 금지. thesis.py 가 UNKNOWN 으로 처리하며,
결측은 절대 퇴출 근거가 되지 않는다 (데이터 사고가 매도로 이어지면 안 되므로).

⚠ 한계 — 정직하게 기록해 둔다: yfinance 의 `info` 는 **현재 시점 스냅샷**이다.
과거 분기의 선행PER·PEG 를 되돌려 받을 수 없어 **논지 재판정 규칙의 역사적
백테스트는 이 데이터 소스로 불가능하다.** 규칙 자체는 오프라인 테스트로 검증하고,
실운용에서는 분기마다 스냅샷을 state 에 누적해 이력을 직접 쌓는다.
"""

FIELDS = {
    "net_margin": "profitMargins",
    "free_cashflow": "freeCashflow",
    "revenue_growth": "revenueGrowth",
    "forward_pe": "forwardPE",
    "peg": "trailingPegRatio",
}


def fetch_fundamentals(ticker: str) -> dict:
    import yfinance as yf
    info = yf.Ticker(ticker).info or {}
    out = {}
    for key, src in FIELDS.items():
        v = info.get(src)
        out[key] = float(v) if isinstance(v, (int, float)) else None
    return out


def fetch_many(tickers) -> dict:
    return {t: fetch_fundamentals(t) for t in tickers}
