"""섀도 리포트 — 폭 구간별 성과·실패원인 + 개선 가설 초안.

표본은 **샘플**(계좌 제약 없이 판정자 통과분 전부)을 쓴다. 계좌는 3~4일이면 꽉 차
그 뒤 폭이 샘플링되지 않고 '돈 남았던 날'만 남아 편향되기 때문이다.

⚠ 샘플 합산 수익률을 "이만큼 벌었다"로 읽지 마라 — 동시에 다 살 수 없었다.
   개별 트레이드 통계(승률·중앙값·실패분류)를 폭 구간별로 보는 용도다.
   "계좌가 얼마 벌었나"는 대시보드 섀도 카드(계좌)가 답한다.

폭 '방향'(오름/내림) 축은 breadth_log.jsonl 에 데이터만 쌓아두고 분석은 아직 안 켠다
— 칸이 늘면 표본이 쪼개지고 헛발견이 는다. 기본 구간이 충분해지면 그때 켠다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader import shadow  # noqa: E402

MIN_SAMPLE = 20                      # 버킷당 이 이상이어야 가설 승격 (p-해킹 방지)
BUCKETS = [(0, 20), (20, 30), (30, 40), (40, 50), (50, 101)]
KINDS = ["목표달성", "갭손절", "1봉손절", "손절", "기간만료", "미체결"]


def _median(xs: list) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _stats(rows: list) -> tuple:
    """(건수, 평균, 중앙값, 승률) — 청산분 기준. 청산 0건이면 None 들."""
    cl = [r for r in rows if r["status"] == "closed"]
    if not cl:
        return len(rows), None, None, None
    rets = [r["ret_pct"] for r in cl]
    wr = sum(1 for x in rets if x > 0) / len(cl) * 100
    return len(rows), sum(rets) / len(rets), _median(rets), wr


def _line(label: str, rows: list) -> None:
    n, avg, med, wr = _stats(rows)
    if avg is None:
        print(f"{label:>10} {n:>5} {'—':>8} {'—':>8} {'—':>6}   (청산 대기)")
    else:
        print(f"{label:>10} {n:>5} {avg:>+7.2f}% {med:>+7.2f}% {wr:>5.0f}%")


def _rank_origin_table(rows: list) -> None:
    """순위대·출처별 성과.

    확장(선정자가 개수를 요구받아 끌어올린 제안)은 확신이 낮은 표본이라
    실계좌가 실제로 쓴 선정과 성격이 다르다. 한 칸에 넣고 평균 내면
    "3~5등까지 사도 되나"는 물론 "지금 2개가 맞나"에도 답을 못 한다.
    """
    has = [r for r in rows if r.get("rank") or r.get("origin")]
    if not has:
        return
    # 버킷 경계를 계좌 컷(MAX_RANK=3)에 맞춘다 — "3등까지"가 옳았는지 보려면
    # 1~3 과 4~5·6+ 가 갈려야 한다. 경계가 어긋나면 그 질문에 답이 안 나온다.
    print(f"\n{'구분':>10} {'건수':>5} {'평균':>8} {'중앙값':>8} {'승률':>6}")
    for lo, hi, lab in ((1, 3, "1~3등"), (4, 5, "4~5등"), (6, 99, "6등+")):
        b = [r for r in has if r.get("rank") and lo <= r["rank"] <= hi]
        if b:
            _line(lab, b)
    print("           " + "-" * 36)
    for org in ("선정자", "확장"):
        b = [r for r in has if r.get("origin") == org]
        if b:
            _line(org, b)
    print("※ '확장'은 개수를 채우라고 시켜서 나온 낮은 확신 제안이다.")
    print("  실계좌가 쓴 '선정자'와 같은 칸에 넣고 평균 내지 마라.")


# 순위 반전 가설 최소 표본 — 각 순위대(상위/하위)가 이만큼은 청산돼야 의심 승격.
# 22건짜리 초기 표본이라 낮게 잡되(각 5), '의심'으로만 낸다(단정 아님).
_REVERSAL_MIN_PER_TIER = 5


def detect_reversal(rows: list, breadth_max: float = 0.30,
                    min_per_tier: int = _REVERSAL_MIN_PER_TIER,
                    gap_pp: float = 3.0) -> dict | None:
    """약세장 모멘텀 반전 탐지 (순수) — 폭 낮을 때 상위 순위가 하위보다 더 깨지나.

    반환: 의심되면 {top_avg, low_avg, n_top, n_low}, 아니면 None.
    조건: 폭<breadth_max 구간에서 각 순위대 표본 min_per_tier 이상 +
          하위평균 − 상위평균 ≥ gap_pp (하위가 그만큼 나음).
    """
    weak = [r for r in rows if r.get("status") == "closed"
            and r.get("breadth") is not None and r["breadth"] < breadth_max
            and r.get("rank")]
    top = [r["ret_pct"] for r in weak if r["rank"] <= 3]
    low = [r["ret_pct"] for r in weak if r["rank"] >= 6]
    if len(top) < min_per_tier or len(low) < min_per_tier:
        return None
    top_avg, low_avg = sum(top) / len(top), sum(low) / len(low)
    if low_avg - top_avg < gap_pp:
        return None
    return {"top_avg": top_avg, "low_avg": low_avg,
            "n_top": len(top), "n_low": len(low)}


def _momentum_reversal_check(rows: list) -> None:
    """현빈의 새 눈 — 반전이 의심되면 가설로 낸다. **지시가 아니라 검증 후보.**

    2026-08-03 관찰: 하락장에서 1~3등 -6%(승률0), 6등+ +1%(승률50)로 뒤집혔다.
    센 종목이 약세장에서 먼저·크게 빠지는 패턴일 수 있다.
    """
    r = detect_reversal(rows)
    if not r:
        return
    print("\n🔬 가설(의심) — 약세장 모멘텀 반전")
    print(f"   폭 30%↓ 구간: 상위(1~3등) 평균 {r['top_avg']:+.1f}%(n={r['n_top']}) "
          f"vs 하위(6등+) {r['low_avg']:+.1f}%(n={r['n_low']})")
    print("   → 약한 시장에선 센 종목이 먼저 크게 빠지는 것으로 보인다. "
          "'폭 낮으면 상위 모멘텀 회피 / 평균회귀' 를 백테스트로 검증해볼 후보.")
    print("   ⚠ 표본 적음 — 가설일 뿐. 라이브 전략 변경은 백테스트·승인 후.")


def main() -> int:
    rows = [r for r in shadow.load_samples()
            if r.get("status") in ("closed", "unfilled")]
    if not rows:
        pend = len(shadow.load_samples())
        print(f"판정 완료된 샘플이 없음 (대기 {pend}건).")
        print("run_shadow_resolve.py 로 체결·청산 판정을 먼저 돌려라.")
        print("(샘플은 프리마켓이 돌 때마다 제약 없이 쌓인다)")
        return 0

    closed = [r for r in rows if r["status"] == "closed"]
    print(f"샘플 {len(rows)}건 (청산 {len(closed)} · 미체결 {len(rows) - len(closed)})")
    print("※ 계좌 수익률 아님 — 폭 구간별 트레이드 통계다\n")

    print(f"{'폭구간':>9} {'건수':>5} {'평균':>8} {'중앙값':>8} {'승률':>6} {'손절%':>6}")
    hyp = []
    for lo, hi in BUCKETS:
        b = [r for r in rows if r.get("breadth") is not None
             and lo <= r["breadth"] * 100 < hi]
        if not b:
            continue
        cl = [r for r in b if r["status"] == "closed"]
        rets = [r["ret_pct"] for r in cl]
        if cl:
            wins = sum(1 for x in rets if x > 0)
            stops = sum(1 for r in cl if r["reason"] == "stop")
            avg, med = sum(rets) / len(rets), _median(rets)
            wr, sr = wins / len(cl) * 100, stops / len(cl) * 100
            print(f"{lo:>3}~{min(hi,100):>3}% {len(b):>5} {avg:>+7.2f}% "
                  f"{med:>+7.2f}% {wr:>5.0f}% {sr:>5.0f}%")
        else:
            print(f"{lo:>3}~{min(hi,100):>3}% {len(b):>5} {'—':>8} {'—':>8} {'—':>6} {'—':>6}")

        # 실패 원인 분포
        cnt = {k: sum(1 for r in b if r.get("failure_kind") == k) for k in KINDS}
        shown = {k: v for k, v in cnt.items() if v}
        if shown:
            print("           " + " · ".join(f"{k} {v}" for k, v in shown.items()))

        # 가설 초안 — 표본 하한 넘고 방향이 뚜렷할 때만
        n = len(b)
        if n >= MIN_SAMPLE and cl:
            if cnt["갭손절"] / n >= 0.4:
                hyp.append((f"{lo}~{hi}%", "강화후보",
                            f"n={n} 갭손절 {cnt['갭손절']}건 — 밤사이 갭에 죽는다. 손절이 못 지키는 구간"))
            if cnt["1봉손절"] / n >= 0.35:
                hyp.append((f"{lo}~{hi}%", "강화후보",
                            f"n={n} 1봉손절 {cnt['1봉손절']}건 — 손절 너무 타이트/진입 타이밍"))
            if cnt["미체결"] / n >= 0.4:
                hyp.append((f"{lo}~{hi}%", "관찰",
                            f"n={n} 미체결 {cnt['미체결']}건 — 지정가가 공격적, 실제론 못 산다"))
            if avg > 0 and med > 0 and wr >= 50:
                hyp.append((f"{lo}~{hi}%", "완화후보",
                            f"n={n} 평균{avg:+.1f}% 중앙값{med:+.1f}% 승률{wr:.0f}% — 조건부 진입 검토"))
            elif med < -2 or sr >= 60:
                hyp.append((f"{lo}~{hi}%", "강화후보",
                            f"n={n} 중앙값{med:+.1f}% 손절{sr:.0f}% — 더 빡세게"))

    print(f"\n※ 표본 {MIN_SAMPLE}건 미만은 가설 승격 안 함 (우연히 좋아 보이는 칸 방지)")
    print("※ 평균만 보지 마라 — 중앙값·손절%가 필터의 보호효과다")

    # ── 순위·출처별 — 섞어서 평균 내면 "몇 등까지 사도 되나"에 답을 못 한다 ──
    _rank_origin_table(rows)
    print("※ 진입은 실전과 동일한 지정가 체결 조건 — 미체결도 기록된다")

    # ── 현빈의 새 눈 — 약세장 모멘텀 반전 자동 탐지 ──
    _momentum_reversal_check(rows)

    if hyp:
        print("\n── 개선 가설 (제안후보) ──")
        for br, kind, note in hyp:
            print(f"  [{kind}] 폭 {br}: {note}")
        print("→ 자동 변경 없음. 제안 카드 흐름(사용자 승인)으로만 필터가 바뀐다.")
    else:
        print("\n표본 충분한 제안후보 없음 — 계속 관찰.")

    bl = shadow.BREADTH_FILE
    if bl.exists():
        n = len([x for x in bl.read_text(encoding="utf-8").splitlines() if x.strip()])
        print(f"\n폭 일지 {n}일치 누적 — 방향(오름/내림) 축 분석은 기본 구간이 "
              f"충분해지면 켠다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
