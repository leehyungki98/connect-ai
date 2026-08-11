"""스윙 전략 정리 (윤가온) — 현빈 사후분석·시장 폭·섀도·최근 선정을 엮어 쉬운 말로.

실행: python scripts/run_swing_brief.py ["질문"]
질문은 인자 또는 env(SWING_BRIEF_Q). 확장에서 호출 시 한글 인자 잘림을 피하려 env 사용.

윤가온의 스윙 쪽 온디맨드 답변 — 현빈의 고정 시각 리포트와 달리 질문에 맞춰 재료를
다시 엮는다. 매매 없음, 순수 관찰 전달.
"""
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autotrader import organizer  # noqa: E402
from autotrader.brain.client import ask_text  # noqa: E402


def _last_jsonl(p: Path):
    if not p.exists():
        return None
    ls = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return json.loads(ls[-1]) if ls else None


def _rows(p: Path) -> list:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _collect() -> list:
    """스윙 재료를 윤가온 블록으로. 각 fact 는 '이미 말이 된' 근거여야 한다."""
    blocks = []

    # 현재 상태 — 실계좌·시장 폭
    status = []
    eq = _last_jsonl(ROOT / "state" / "equity_log.jsonl")
    if eq:
        status.append(f"실계좌 평가액 {eq['equity_krw']:,}원, 보유 {eq.get('n_positions', 0)}종목")
    br_rows = _rows(ROOT / "ledger" / "shadow" / "breadth_log.jsonl")
    if br_rows:
        recent = br_rows[-5:]
        trend = " → ".join(f"{int(r['breadth']*100)}%" for r in recent)
        last = int(recent[-1]["breadth"] * 100)
        status.append(f"시장 폭(강한 종목 비율) 최근 추이 {trend}")
        status.append(f"지금 {last}%로 기준 50% "
                      + ("밑이라 실매수 중단 중 — 약한 시장으로 판단해 관망"
                         if last < 50 else "위라 매수 가능 구간"))
    if status:
        blocks.append({"source": "현재 상태", "facts": status})

    # 현빈 사후분석 (최근) — 이미 쉬운 말 보고서라 통째로 재료
    rev_dir = ROOT / "ledger" / "reviews"
    if rev_dir.exists():
        revs = sorted(p for p in rev_dir.glob("*.md") if p.stem[:4].isdigit())
        if revs:
            body = revs[-1].read_text(encoding="utf-8").strip()
            facts = [ln.strip("# ").strip() for ln in body.splitlines()
                     if ln.strip() and not ln.strip().startswith("---")][:14]
            blocks.append({"source": f"현빈 사후분석 ({revs[-1].stem})", "facts": facts})

    # 섀도(가상매매) — 성적·실패 분류
    sv_p = ROOT / "state" / "shadow_view.json"
    sh = []
    if sv_p.exists():
        v = json.loads(sv_p.read_text(encoding="utf-8"))
        sh.append(f"가상매매 보유 {len(v.get('positions', []))}종목, "
                  f"평가손익 {v.get('total_pnl_krw', 0):+,}원 ({v.get('total_ret_pct', 0):+}%)")
    samples = _rows(ROOT / "ledger" / "shadow" / "shadow_samples.jsonl")
    closed = [r for r in samples if r.get("status") == "closed"]
    if closed:
        kinds = Counter(r.get("failure_kind", "?") for r in closed)
        sh.append("청산된 가상매매 실패·성공 분류: "
                  + ", ".join(f"{k} {n}건" for k, n in kinds.most_common()))
    elif samples:
        sh.append(f"가상매매 표본 {len(samples)}건 쌓였으나 아직 청산된 게 없어 성적은 미확정")
    if sh:
        blocks.append({"source": "섀도(실제 주문 없는 가상매매)", "facts": sh})

    # 최근 선정 — 오늘/최근 프리마켓의 매매·차단 사유
    pm = _rows(ROOT / "state" / "premarket_log.jsonl")
    if pm:
        last = pm[-1]
        picks = []
        picks.append(f"{last.get('date')}: 실매수 {len(last.get('buys_placed', []))}건, "
                     f"청산 {len(last.get('exits_placed', []))}건")
        for s in (last.get("skipped") or [])[:4]:
            picks.append(f"건너뜀 사유: {s}")
        blocks.append({"source": "최근 선정 기록", "facts": picks})

    return blocks


def main() -> int:
    question = " ".join(sys.argv[1:]).strip() or os.environ.get("SWING_BRIEF_Q", "").strip()
    blocks = _collect()
    if not blocks:
        print("정리할 스윙 기록이 없어요 — 파이프라인이 한 번은 돌아야 재료가 생겨요.")
        return 0
    result = organizer.synthesize(question, blocks, runner=lambda p: ask_text(p, brain="claude"))
    print(organizer.render(result, asof=date.today().isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
