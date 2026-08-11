"""재무 스냅샷 누적 — 미래의 진짜 백테스트를 위해 오늘의 시점 데이터를 남긴다.

## 왜 ledger/ 인가 (state/ 가 아니라)
`state/` 는 "재생성 가능한 운영 데이터" 다. 이 스냅샷은 **재생성 불가**다:
- yfinance 연간 재무제표는 5개 연도만 준다 → 6년 전 데이터는 영원히 못 받는다
- **선행PER·PEG 는 시점 데이터**다. 오늘 찍지 않으면 내일은 다른 값이고,
  과거값은 어디서도 복구할 수 없다
2026-07-20 검증에서 논지 재판정 규칙을 역사적으로 백테스트할 수 없었던 이유가
정확히 이것이다. 오늘부터 쌓으면 몇 년 뒤엔 가능해진다.

## 왜 넓게 찍는가
1. **오늘 규칙이 쓰는 지표만 저장하면 내일 바뀐 규칙을 검증할 수 없다.**
   기준이 바뀌거나 추가될 걸 전제하고 여유 있게 저장한다.
2. **보유 종목만 저장하면 "그때 PLTR 을 넣었어야 했나" 를 답할 수 없다.**
   관찰 목록·기각 종목·벤치마크까지 찍는다 (config.SNAPSHOT_WATCHLIST).

## 멱등성
같은 날 여러 번 돌려도 레코드는 (티커, 날짜)당 하나다 — 마지막 값이 이긴다.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from .store import LEDGER_DIR

SCHEMA_VERSION = 1

# 넓게 저장한다 — 오늘 안 쓰는 지표도 나중 규칙이 쓸 수 있다.
CAPTURE_FIELDS = {
    # 논지 3기준이 지금 쓰는 것
    "net_margin": "profitMargins",
    "free_cashflow": "freeCashflow",
    "revenue_growth": "revenueGrowth",
    "forward_pe": "forwardPE",
    "peg": "trailingPegRatio",
    # 지금은 안 쓰지만 규칙이 바뀔 때 필요할 수 있는 것들
    "trailing_pe": "trailingPE",
    "price_to_book": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months",
    "gross_margin": "grossMargins",
    "operating_margin": "operatingMargins",
    "earnings_growth": "earningsGrowth",
    "debt_to_equity": "debtToEquity",
    "return_on_equity": "returnOnEquity",
    "market_cap": "marketCap",
    "beta": "beta",
    "current_price": "currentPrice",
    "shares_outstanding": "sharesOutstanding",
}


def _num(v):
    """None/NaN/비수치를 전부 None 으로 — 결측이 조용히 0 이나 FAIL 이 되면 안 된다."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return None if v != v else float(v)


def capture(ticker: str, info: dict, note: str = "", today: date = None) -> dict:
    """yfinance info 스냅샷 1건 생성 (저장은 store_record)."""
    today = today or date.today()
    return {
        "schema": SCHEMA_VERSION,
        "ticker": ticker,
        "date": today.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance.Ticker.info",
        "note": note,
        "metrics": {k: _num(info.get(src)) for k, src in CAPTURE_FIELDS.items()},
        "missing": sorted(k for k, src in CAPTURE_FIELDS.items()
                          if _num(info.get(src)) is None),
    }


def path_for(ticker: str, ledger_dir: Path = None) -> Path:
    base = Path(ledger_dir) if ledger_dir else LEDGER_DIR / "fundamentals"
    return base / f"{ticker}.jsonl"


def store_record(record: dict, ledger_dir: Path = None) -> Path:
    """멱등 적재 — 같은 (티커, 날짜) 는 덮어쓴다."""
    p = path_for(record["ticker"], ledger_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load(record["ticker"], ledger_dir)
    merged = {r["date"]: r for r in existing}
    merged[record["date"]] = record
    with open(p, "w", encoding="utf-8") as f:
        for d in sorted(merged):
            f.write(json.dumps(merged[d], ensure_ascii=False) + "\n")
    return p


def load(ticker: str, ledger_dir: Path = None) -> list:
    """시간순 스냅샷 목록."""
    p = path_for(ticker, ledger_dir)
    if not p.exists():
        return []
    out = [json.loads(line) for line in
           p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(out, key=lambda r: r["date"])


def as_fundamentals(record: dict) -> dict:
    """thesis.judge_criteria 가 먹는 형태로 변환."""
    m = record["metrics"]
    return {k: m.get(k) for k in
            ("net_margin", "free_cashflow", "revenue_growth", "forward_pe", "peg")}


def coverage(ticker: str, ledger_dir: Path = None) -> dict:
    """이 종목의 이력이 얼마나 쌓였는지 — 진짜 백테스트까지 얼마나 남았나."""
    recs = load(ticker, ledger_dir)
    if not recs:
        return {"ticker": ticker, "count": 0, "first": None, "last": None,
                "quarters": 0}
    quarters = {r["date"][:4] + "-Q" + str((int(r["date"][5:7]) - 1) // 3 + 1)
                for r in recs}
    return {"ticker": ticker, "count": len(recs), "first": recs[0]["date"],
            "last": recs[-1]["date"], "quarters": len(quarters)}
