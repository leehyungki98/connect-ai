"""오프라인 fixture — 네트워크 금지. 가격은 전부 합성값."""
from longcore.config import BUCKETS

CFG = BUCKETS["해외증권"]

PRICES = {"VOO": 500.0, "NVDA": 200.0, "TSM": 400.0, "META": 600.0}
USDKRW = 1400.0


def holding_at_targets(nav: float = 20_000.0) -> dict:
    """목표 비중 정확히 맞는 보유 (ETF 60 / 성장주 각 10 / 현금 10)."""
    return {
        "cash_usd": nav * 0.10,
        "positions": {
            "VOO": {"shares": nav * 0.60 / PRICES["VOO"]},
            "NVDA": {"shares": nav * 0.10 / PRICES["NVDA"]},
            "TSM": {"shares": nav * 0.10 / PRICES["TSM"]},
            "META": {"shares": nav * 0.10 / PRICES["META"]},
        },
        "initialized": True,
    }


def cash_only_holding(cash: float = 21_000.0) -> dict:
    return {"cash_usd": cash, "positions": {}, "initialized": False}
