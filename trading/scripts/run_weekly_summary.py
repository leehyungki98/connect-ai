"""주간 요약 — 한 주 동안 뭐가 있었나. 쉬운 말로, 짧게.

읽는 사람은 개발자가 아니라 돈의 주인이다. 전문용어·개발 얘기 없이 쓴다.
금요일 마감 후 1회 실행 (스케줄).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader import shadow  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _last_7_days() -> tuple:
    today = date.today()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


def _breadth_rows(since: str) -> list:
    if not shadow.BREADTH_FILE.exists():
        return []
    rows = [json.loads(x) for x in
            shadow.BREADTH_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r["date"] >= since]


def _equity_rows(since: str) -> list:
    p = ROOT / "state" / "equity_log.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("date", "") >= since:
            out.append(r)
    return out


def main() -> int:
    since, today = _last_7_days()
    print(f"📅 주간 요약 · {since} ~ {today}\n")

    # ── 실계좌 ──
    eq = _equity_rows(since)
    if eq:
        last = eq[-1]
        traded = sum(1 for r in eq if r.get("n_positions", 0) > 0)
        print(f"💰 실계좌  {last['equity_krw']:,}원 · 보유 {last.get('n_positions', 0)}종목")
        print(f"   이번 주 매매한 날: {traded}일 / {len(eq)}일")
    else:
        print("💰 실계좌  기록 없음")

    # ── 시장 폭 추이 ──
    br = _breadth_rows(since)
    if br:
        vals = [r["breadth"] for r in br]
        blocked = sum(1 for v in vals if v < 0.5)
        arrow = "↘ 나빠지는 중" if len(vals) >= 2 and vals[-1] < vals[0] else (
            "↗ 좋아지는 중" if len(vals) >= 2 and vals[-1] > vals[0] else "→ 비슷")
        print(f"\n📊 시장 상태  {vals[0]:.0%} → {vals[-1]:.0%}  {arrow}")
        print(f"   기준(50%) 밑이라 못 산 날: {blocked}일 / {len(vals)}일")
        print("   " + " ".join(f"{r['date'][5:]} {r['breadth']:.0%}" for r in br[-5:]))
    else:
        print("\n📊 시장 상태  기록 없음")

    # ── 섀도 (가상 매매) ──
    samples = shadow.load_samples()
    wk = [r for r in samples if r.get("date", "") >= since]
    closed = [r for r in samples if r.get("status") == "closed"]
    st = shadow.load_state()
    print(f"\n🌓 가상 매매  이번 주 {len(wk)}건 골라봄 · 누적 청산 {len(closed)}건")
    if closed:
        wins = [r for r in closed if r["ret_pct"] > 0]
        avg = sum(r["ret_pct"] for r in closed) / len(closed)
        print(f"   맞춘 비율 {len(wins)}/{len(closed)} ({len(wins)/len(closed):.0%}) · "
              f"평균 {avg:+.1f}%")
        kinds = {}
        for r in closed:
            kinds[r.get("failure_kind", "?")] = kinds.get(r.get("failure_kind", "?"), 0) + 1
        print("   결과: " + " · ".join(f"{k} {v}건" for k, v in
                                     sorted(kinds.items(), key=lambda x: -x[1])))
    else:
        print("   아직 팔린 게 없어서 성적은 다음 주에")
    pos = st.get("positions", {})
    if pos:
        print(f"   지금 들고 있는 것: " + ", ".join(
            f"{p.get('name', s)}" for s, p in pos.items()))

    # ── 한 줄 결론 ──
    print("\n💬 한 줄")
    if br and sum(1 for v in [r["breadth"] for r in br] if v < 0.5) == len(br):
        print("   이번 주는 시장이 약해서 계속 관망했습니다. 가상 매매로 데이터만 쌓는 중입니다.")
    elif eq and any(r.get("n_positions", 0) > 0 for r in eq):
        print("   이번 주 실제 매매가 있었습니다. 위 성적을 확인하세요.")
    else:
        print("   특이사항 없습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
