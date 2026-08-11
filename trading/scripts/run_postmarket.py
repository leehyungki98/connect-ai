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
from autotrader.status import write_gate_status
from smoke_kis import load_env


# 폰으로 보낼 섹션. "판단 품질"·"리스크 관찰"은 길고 표가 많아 파일에서 읽는 게 낫다.
SECTIONS_FOR_REPORT = ("오늘 요약", "개선 후보")


def _digest(review: Path, wanted: tuple[str, ...]) -> list[str]:
    """리뷰 마크다운에서 지정 섹션만 뽑는다. 없으면 빈 목록 (보고는 계속된다)."""
    try:
        text = review.read_text(encoding="utf-8")
    except OSError:
        return []
    blocks, current, keep = [], [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if keep and current:
                blocks.append("\n".join(current).rstrip())
            title = line[3:].strip()
            keep = any(w in title for w in wanted)
            current = [f"── {title} ──"] if keep else []
            continue
        if keep:
            current.append(line)
    if keep and current:
        blocks.append("\n".join(current).rstrip())
    return blocks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--review", action="store_true", help="LLM 사후분석 실행")
    p.add_argument("--brain", default="claude", choices=["claude", "codex"])
    args = p.parse_args()

    env = load_env(ROOT / ".env")
    kis = KISPaperClient(env["KIS_APPKEY"], env["KIS_APPSECRET"], env["KIS_ACCOUNT"])

    # 사후분석은 재생성 불가한 학습 자산 — 커밋 금지 구역인 state/ 밖에 남긴다.
    LEDGER_DIR = ROOT / "ledger"
    s = run_postmarket(
        kis, STATE_DIR, date.today(), brain=args.brain, llm_review=args.review,
        review_dir=LEDGER_DIR / "reviews",
    )
    write_gate_status(STATE_DIR, date.today(), s["equity_krw"])  # 게이트 상태판 갱신
    ret = s["daily_return_bp"]
    print(f"[postmarket] 총평가 {s['equity_krw']:,}원, 현금 {s['cash_krw']:,}원, "
          f"보유 {s['n_positions']}종목")
    print(f"[postmarket] 일일 수익률(관찰용): "
          f"{f'{ret / 100:+.2f}%' if ret is not None else 'N/A'}")
    if s["review_file"]:
        print(f"[postmarket] 사후분석 저장: {s['review_file']}")
        # 분석 본문을 stdout 에 실어야 텔레그램까지 간다. 예전엔 저장 경로만
        # 찍혀서, 정작 읽어야 할 3천 자 분석은 파일에만 남고 폰에는 배관
        # 로그 네 줄만 갔다. 폰 가독성을 위해 핵심 섹션만 옮긴다.
        for block in _digest(Path(s["review_file"]), SECTIONS_FOR_REPORT):
            print(block)
    if s["review_error"]:
        print(f"[postmarket] 사후분석 실패(스냅샷은 기록됨): {s['review_error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
