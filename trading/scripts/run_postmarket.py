"""마감 후 1회 실행: 평가액 스냅샷 기록 + (선택) LLM 사후분석.

사용법: python scripts/run_postmarket.py [--review] [--brain claude|codex]
필요: .env (KIS 키). --review는 claude/codex CLI 로그인 필요.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autotrader.config import STATE_DIR
from autotrader.kis.client import KISPaperClient
from autotrader.review import run_postmarket
from smoke_kis import load_env


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--review", action="store_true", help="LLM 사후분석 실행")
    p.add_argument("--brain", default="claude", choices=["claude", "codex"])
    args = p.parse_args()

    env = load_env(ROOT / ".env")
    kis = KISPaperClient(env["KIS_APPKEY"], env["KIS_APPSECRET"], env["KIS_ACCOUNT"])

    s = run_postmarket(
        kis, STATE_DIR, date.today(), brain=args.brain, llm_review=args.review
    )
    ret = s["daily_return_bp"]
    print(f"[postmarket] 총평가 {s['equity_krw']:,}원, 현금 {s['cash_krw']:,}원, "
          f"보유 {s['n_positions']}종목")
    print(f"[postmarket] 일일 수익률(관찰용): "
          f"{f'{ret / 100:+.2f}%' if ret is not None else 'N/A'}")
    if s["review_file"]:
        print(f"[postmarket] 사후분석 저장: {s['review_file']}")
    if s["review_error"]:
        print(f"[postmarket] 사후분석 실패(스냅샷은 기록됨): {s['review_error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
