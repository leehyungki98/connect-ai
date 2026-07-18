"""결정적 페이퍼 체결 모델. 절대규칙 2: 정수(원) 연산, 같은 입력 → 같은 출력.

낙관 편향 방지 원칙:
- 수수료 0.015% (매수/매도 각각, 올림)
- 매도 제세금 0.18% (증권거래세+농특세, 올림)
- 슬리피지 0.05%를 항상 불리한 방향으로 (매수 위로, 매도 아래로)
- 부분체결: 당일 거래량의 1%까지만 체결
- 갭: 매수 갭상승 시 지정가 초과면 미체결, 매도 갭하락 시 시가 체결

net_krw 부호: 매수 = 음수(현금 유출), 매도 = 양수(현금 유입).
"""
from __future__ import annotations

from dataclasses import dataclass

COMMISSION_PPM = 150            # 0.015%
SELL_TAX_PPM = 1_800            # 0.18%
SLIPPAGE_PPM = 500              # 0.05%
PARTICIPATION_CAP_PPM = 10_000  # 당일 거래량의 1%


def _ceil_ppm(amount: int, ppm: int) -> int:
    return -(-amount * ppm // 1_000_000)


def max_fill_qty(day_volume: int) -> int:
    return max(day_volume, 0) * PARTICIPATION_CAP_PPM // 1_000_000


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str            # "buy" | "sell"
    qty: int             # 실제 체결 수량 (부분체결 반영)
    price: int           # 체결 단가 (슬리피지 반영)
    commission_krw: int
    tax_krw: int
    net_krw: int         # 매수: -(대금+수수료), 매도: 대금-수수료-세금


def _make_buy(symbol: str, qty: int, price: int) -> Fill:
    gross = qty * price
    comm = _ceil_ppm(gross, COMMISSION_PPM)
    return Fill(symbol, "buy", qty, price, comm, 0, -(gross + comm))


def _make_sell(symbol: str, qty: int, price: int) -> Fill:
    gross = qty * price
    comm = _ceil_ppm(gross, COMMISSION_PPM)
    tax = _ceil_ppm(gross, SELL_TAX_PPM)
    return Fill(symbol, "sell", qty, price, comm, tax, gross - comm - tax)


def fill_limit_buy(
    symbol: str, limit_price: int, qty: int,
    day_open: int, day_low: int, day_volume: int,
) -> Fill | None:
    """지정가 매수. 저가가 지정가 이하일 때만 체결."""
    if qty <= 0 or limit_price <= 0 or day_low > limit_price:
        return None
    filled = min(qty, max_fill_qty(day_volume))
    if filled <= 0:
        return None
    base = day_open if day_open <= limit_price else limit_price
    price = min(limit_price, base + _ceil_ppm(base, SLIPPAGE_PPM))
    return _make_buy(symbol, filled, price)


def fill_stop_sell(
    symbol: str, stop_price: int, qty: int,
    day_open: int, day_low: int, day_volume: int,
) -> Fill | None:
    """손절 매도. 저가가 손절가 이하로 내려오면 체결. 갭하락이면 시가 기준."""
    if qty <= 0 or stop_price <= 0 or day_low > stop_price:
        return None
    filled = min(qty, max_fill_qty(day_volume))
    if filled <= 0:
        return None
    base = min(day_open, stop_price)
    price = base - _ceil_ppm(base, SLIPPAGE_PPM)
    return _make_sell(symbol, filled, price)


def fill_market_sell(
    symbol: str, qty: int, day_open: int, day_volume: int,
) -> Fill | None:
    """시가 매도 (기간만료 청산용). 시가 - 슬리피지로 체결."""
    if qty <= 0 or day_open <= 0:
        return None
    filled = min(qty, max_fill_qty(day_volume))
    if filled <= 0:
        return None
    price = day_open - _ceil_ppm(day_open, SLIPPAGE_PPM)
    return _make_sell(symbol, filled, price)


def fill_limit_sell(
    symbol: str, target_price: int, qty: int,
    day_open: int, day_high: int, day_volume: int,
) -> Fill | None:
    """목표가 지정가 매도. 고가가 목표가 이상일 때만 체결, 목표가 미만으로는 안 판다."""
    if qty <= 0 or target_price <= 0 or day_high < target_price:
        return None
    filled = min(qty, max_fill_qty(day_volume))
    if filled <= 0:
        return None
    base = max(day_open, target_price)
    price = max(target_price, base - _ceil_ppm(base, SLIPPAGE_PPM))
    return _make_sell(symbol, filled, price)
