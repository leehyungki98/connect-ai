"""섀도 리포트 — 폭 구간별 청산 성과 + 필터 개선 가설 자동 초안.

"이 폭에서는 이 정도 하면 이득이 이 정도" 를 숫자로. 필터의 보호효과(중앙값·승률·
동시손절)와 놓친 이익을 **나란히** 보여준다 — 한쪽만 보면 규율이 침식된다.
가설은 표본 하한 넘어야 '제안후보' 로 승격. 자동 변경 없음 — 제안 카드 흐름으로만.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader import shadow  # noqa: E402

MIN_SAMPLE = 20      # 버킷당 청산 이 개수 넘어야 '제안후보'
BUCKETS = [(0, 20), (20, 30), (30, 40), (40, 50), (50, 101)]


def _load_exits() -> list:
    if not shadow.TRADES_FILE.exists():
        return []
    exits = []
    for line in shadow.TRADES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("event") == "exit":
            exits.append(r)
    return exits


def _entry_breadth() -> dict:
    """symbol+entry_date → breadth (진입 이벤트에서). 청산에 폭을 붙이려고."""
    m = {}
    if shadow.TRADES_FILE.exists():
        for line in shadow.TRADES_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("event") == "entry" and r.get("breadth") is not None:
                m[(r["symbol"], r["date"])] = r["breadth"]
    return m


def _median(xs: list) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def main() -> int:
    exits = _load_exits()
    eb = _entry_breadth()
    if not exits:
        print("청산된 섀도가 없음 — run_shadow_resolve.py 로 판정 먼저.")
        print("(섀도 진입은 프리마켓이 돌 때마다 쌓인다)")
        return 0

    # 폭 버킷별 집계
    print(f"섀도 청산 {len(exits)}건 — 폭 구간별 성과\n")
    print(f"{'폭구간':>8} {'건수':>5} {'평균':>8} {'중앙값':>8} {'승률':>6} "
          f"{'손절%':>6} {'평균보유':>8}")
    hypotheses = []
    for lo, hi in BUCKETS:
        rows = [e for e in exits
                if (b := eb.get((e["symbol"], e["entry_date"]))) is not None
                and lo <= b * 100 < hi]
        if not rows:
            continue
        rets = [e["ret_pct"] for e in rows]
        wins = sum(1 for r in rets if r > 0)
        stops = sum(1 for e in rows if e["reason"] == "stop")
        avg, med = sum(rets) / len(rets), _median(rets)
        wr, sr = wins / len(rows) * 100, stops / len(rows) * 100
        avg_hold = sum(e["hold_days"] for e in rows) / len(rows)
        print(f"{lo:>3}~{hi if hi <= 100 else 100:>2}%{'':>1} {len(rows):>5} "
              f"{avg:>+7.2f}% {med:>+7.2f}% {wr:>5.0f}% {sr:>5.0f}% {avg_hold:>6.1f}일")

        # 가설 자동 초안 — 표본 하한 넘고 방향이 뚜렷할 때만
        if len(rows) >= MIN_SAMPLE:
            if avg > 0 and med > 0 and wr >= 50:
                hypotheses.append((f"{lo}~{hi}%", "완화후보",
                                   f"n={len(rows)} 평균{avg:+.1f}% 중앙값{med:+.1f}% 승률{wr:.0f}% — 이 폭 조건부 진입 검토"))
            elif med < -2 or sr >= 60:
                hypotheses.append((f"{lo}~{hi}%", "강화후보",
                                   f"n={len(rows)} 중앙값{med:+.1f}% 손절{sr:.0f}% — 이 폭 더 빡세게(쿨다운/랭크상한) 검토"))

    print(f"\n※ 표본 {MIN_SAMPLE}건 미만 버킷은 가설 승격 안 함 (p-해킹 방지).")
    print("※ 평균만 X — 중앙값·손절%가 필터의 보호효과다. 둘 다 보고 판단.")

    if hypotheses:
        print("\n── 개선 가설 (제안후보) ──")
        for br, kind, note in hypotheses:
            print(f"  [{kind}] 폭 {br}: {note}")
        print("→ observations.jsonl 에 적재하려면 사람이 확인 후 기록. 자동 변경 없음.")
    else:
        print("\n표본 충분한 제안후보 없음 — 계속 관찰.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
