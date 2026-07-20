"""실효 비중(look-through) — ETF 안에 든 같은 종목까지 합산한 진짜 노출.

왜 필요한가: VOO 60% 안에 NVDA 가 ~7.5% 들어있다. 직접 보유 10% 와 합치면
실효 노출은 14.5% 다. 직접분만 보는 상한은 이걸 놓친다 (2026-07-20 설계 개정).

순수 함수. 지수 구성 비중은 config.INDEX_LOOKTHROUGH 하드코딩 상수를 받는다
(결정적 코드 원칙 — 외부 조회는 값이 조용히 변해 재현성을 깬다).
"""


def effective_weights(positions: dict, cash_usd: float, prices: dict,
                      index_lookthrough: dict) -> dict:
    """{티커: 실효 비중}. positions 는 {티커: {"shares": n}}.

    실효 = 직접 보유 비중 + Σ(보유 ETF 비중 × 그 ETF 안의 해당 종목 비중)
    """
    values = {t: p["shares"] * prices[t] for t, p in positions.items()}
    nav = cash_usd + sum(values.values())
    if nav <= 0:
        raise ValueError(f"NAV 가 양수가 아님: {nav}")

    direct = {t: v / nav for t, v in values.items()}
    eff = dict(direct)
    for etf, components in index_lookthrough.items():
        etf_w = direct.get(etf, 0.0)
        if etf_w <= 0:
            continue
        for ticker, share in components.items():
            eff[ticker] = eff.get(ticker, 0.0) + etf_w * share
    return eff


def breaches(effective: dict, cap: float, watched) -> list:
    """상한 초과 종목 [(티커, 실효비중)]. watched = 상한을 적용할 종목 집합."""
    return sorted(
        ((t, w) for t, w in effective.items() if t in watched and w > cap),
        key=lambda x: -x[1])
