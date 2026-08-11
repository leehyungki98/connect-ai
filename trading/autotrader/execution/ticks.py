"""KRX 호가단위 보정. 호가단위 미준수 주문은 KIS가 거부하므로 주문 전 내림 보정.

2023-01 개편 기준 호가단위:
  <2,000: 1 / <5,000: 5 / <20,000: 10 / <50,000: 50
  / <200,000: 100 / <500,000: 500 / 이상: 1,000
"""
_BANDS = [(2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
          (200_000, 100), (500_000, 500)]


def round_down_to_tick(price: int) -> int:
    if price <= 0:
        return 0
    for threshold, tick in _BANDS:
        if price < threshold:
            return price - price % tick
    return price - price % 1_000
