"""재무 스냅샷 적재 — 오늘의 시점 데이터를 ledger/fundamentals/ 에 남긴다.

선행PER·PEG 는 오늘 안 찍으면 영원히 복구 불가다. 논지 재판정 규칙을 역사적으로
검증할 수 없었던 이유가 그것이다 (ledger/reviews/2026-07-20_thesis_rule_validation.md).
오늘부터 쌓으면 몇 년 뒤엔 가능해진다.

권장 주기: **월 1회 이상**. 규칙은 분기 단위지만 분기에 1점만 찍으면 표본이 너무
느리게 쌓이고, 분기 중간의 급변을 놓친다. 멱등하므로 자주 돌려도 안전하다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import snapshot  # noqa: E402
from longcore.config import BUCKETS, SNAPSHOT_WATCHLIST  # noqa: E402


def main() -> int:
    cfg = BUCKETS["해외증권"]
    held = {t: "보유" for comp in cfg["sleeves"].values() for t in comp}
    targets = {**held, **SNAPSHOT_WATCHLIST}

    import yfinance as yf
    print(f"재무 스냅샷 — 대상 {len(targets)}종 (보유 {len(held)} + "
          f"관찰·기각·벤치마크 {len(targets) - len(held)})\n")

    failures = []
    for ticker in sorted(targets):
        try:
            info = yf.Ticker(ticker).info or {}
            rec = snapshot.capture(ticker, info, note=targets[ticker])
            snapshot.store_record(rec)
            got = len(snapshot.CAPTURE_FIELDS) - len(rec["missing"])
            cov = snapshot.coverage(ticker)
            print(f"  {ticker:<5} {got:>2}/{len(snapshot.CAPTURE_FIELDS)} 지표 "
                  f"· 누적 {cov['count']}건 ({cov['first']} ~ {cov['last']}) "
                  f"· {targets[ticker]}")
            if rec["missing"]:
                print(f"        결측: {', '.join(rec['missing'])}")
        except Exception as e:      # 한 종목 실패가 나머지를 막지 않게
            failures.append((ticker, str(e)))
            print(f"  {ticker:<5} 실패: {e}")

    # 논지 재판정 대상은 성장주뿐이다 — ETF 는 3기준(순이익률·매출성장·PER)이
    # 적용되지 않으므로 누적 현황에서 뺀다. 스냅샷 자체는 가격 맥락용으로 남긴다.
    print("\n누적 현황 — 논지 규칙의 자체 백테스트 가능 시점 (성장주만):")
    for ticker in sorted(cfg["sleeves"].get("GROWTH", {})):
        cov = snapshot.coverage(ticker)
        need = max(0, 8 - cov["quarters"])     # 2년치(8분기)를 최소 표본으로 본다
        state = "가능" if need == 0 else f"{need}분기 더 필요"
        print(f"  {ticker:<5} {cov['quarters']}분기 누적 → {state}")
    print("  ※ 월 1회 이상 실행 권장. 선행PER·PEG 는 오늘 안 찍으면 복구 불가다.")

    if failures:
        print(f"\n⚠ 실패 {len(failures)}건 — 다음 실행에서 재시도됨 "
              f"(멱등이라 중복 걱정 없음)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
