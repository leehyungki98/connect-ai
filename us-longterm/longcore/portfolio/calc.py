"""보유·NAV·비중·환노출 계산 — 순수 함수만. 가격 누락 시 예외 (fail-closed)."""


def _ticker_sleeve_map(sleeves: dict) -> dict:
    return {t: s for s, comp in sleeves.items() for t in comp}


def nav_usd(holding: dict, prices: dict) -> float:
    total = holding["cash_usd"]
    for ticker, pos in holding.get("positions", {}).items():
        total += pos["shares"] * prices[ticker]   # KeyError = 가격 누락, 위로 전파
    return total


def sleeve_values(holding: dict, prices: dict, sleeves: dict) -> dict:
    """슬리브별 평가액 (USD). CASH = 현금. 미지의 보유 종목은 예외."""
    t2s = _ticker_sleeve_map(sleeves)
    values = {s: 0.0 for s in sleeves}
    values["CASH"] = holding["cash_usd"]
    for ticker, pos in holding.get("positions", {}).items():
        if ticker not in t2s:
            raise ValueError(f"슬리브에 없는 보유 종목: {ticker}")
        values[t2s[ticker]] += pos["shares"] * prices[ticker]
    return values


def weights(holding: dict, prices: dict, sleeves: dict) -> dict:
    values = sleeve_values(holding, prices, sleeves)
    nav = sum(values.values())
    if nav <= 0:
        raise ValueError(f"NAV 가 양수가 아님: {nav}")
    return {s: v / nav for s, v in values.items()}


def fx_exposure(holding: dict, prices: dict, usdkrw: float) -> dict:
    """환노출 — 이 데스크는 전 자산 USD 표시라 사실상 100%.
    금액(USD)과 KRW 환산 NAV 를 함께 기록한다 (헤지 안 하지만 측정은 한다)."""
    nav = nav_usd(holding, prices)
    usd_assets = nav   # 현금 포함 전부 USD 표시
    return {
        "usd_exposure_pct": (usd_assets / nav) if nav > 0 else 0.0,
        "nav_usd": nav,
        "usdkrw": usdkrw,
        "nav_krw": nav * usdkrw,
    }
