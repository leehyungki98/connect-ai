"""백테스트 미션 스윕 (BACKTEST_MISSION.txt 수행 도구).

사용법:
  python scripts/run_sweep.py cache      KRX 1회 수집 → state/bars_cache.json (이후 오프라인)
  python scripts/run_sweep.py verify     캐시로 V0 전/후반 재현 → 기존 compare JSON과 대조 (2회 소모)
  python scripts/run_sweep.py diagnose   0단계: 실패 패턴 수치 검증 + σ20 지연/클램프 분석 (실행 0회)
  python scripts/run_sweep.py sweep      순차 축 탐색 P2→P1→P3→P3' (게이트: 양쪽 구간 MDD↓·샤프↑)
  python scripts/run_sweep.py ext --params '{"cooldown_bars":5,...}'   확장 1조합 실행 (2회 소모)
  python scripts/run_sweep.py report     state/backtest_mission_report.md 생성

예산: 모든 run_backtest() 호출을 state/sweep_runs.jsonl에 기록, 상한 40회 (미션 제약).
같은 (파라미터, 구간) 조합은 원장에서 찾아 재사용 — 같은 실험을 반복하지 않는다.
"""
import argparse
import datetime as dt
import json
import statistics
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from autotrader.backtest import BaselineParams, Bar, run_backtest
from autotrader.config import INITIAL_CAPITAL_KRW, STATE_DIR

RUN_CAP = 40
CACHE = STATE_DIR / "bars_cache.json"
LEDGER = STATE_DIR / "sweep_runs.jsonl"
# 실험 원장·진단은 재생성 불가한 학습 자산 — 커밋 금지 구역인 state/ 밖에 둔다.
LEDGER_DIR = ROOT / "ledger"
EXPERIMENTS = LEDGER_DIR / "experiments.md"
DIAGNOSIS = LEDGER_DIR / "diagnosis.json"
COMPARE_JSON = STATE_DIR / "backtest_compare_20250716_20260716.json"
V0_FULL_JSON = STATE_DIR / "backtest_20250716_20260716.json"
START, END = "20250716", "20260716"


# ---------- 캐시 ----------

def build_cache() -> None:
    if CACHE.exists():
        print(f"[cache] 이미 존재: {CACHE} — 재수집 안 함 (미션 제약 4)")
        return
    from run_backtest import export_krx_env, fetch_bars
    export_krx_env()
    from autotrader.screener.universe import fetch_universe

    print(f"[cache] 유니버스 선정(asof={START})...")
    universe = fetch_universe(asof=START)
    symbols = [s.symbol for s in universe]
    print(f"[cache] {len(symbols)}종목 일봉 수집 (0.25s 페이싱)...")
    data = fetch_bars(symbols, START, END)
    payload = {
        "meta": {"start": START, "end": END, "symbols": sorted(data)},
        "bars": {
            sym: [[b.date, b.open, b.high, b.low, b.close, b.volume] for b in bars]
            for sym, bars in data.items()
        },
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[cache] 저장: {CACHE} ({len(data)}종목)")


def load_cache() -> dict[str, list[Bar]]:
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    return {
        sym: [Bar(*row) for row in rows] for sym, rows in raw["bars"].items()
    }


def split_halves(data: dict[str, list[Bar]]):
    """run_backtest.py run_compare()와 동일한 분할 (중앙 날짜, 전반 ≤ mid < 후반)."""
    dates = sorted({b.date for bars in data.values() for b in bars})
    mid = dates[len(dates) // 2]
    h1 = {s: [b for b in bs if b.date <= mid] for s, bs in data.items()}
    h2 = {s: [b for b in bs if b.date > mid] for s, bs in data.items()}
    return mid, h1, h2


# ---------- 예산 원장 ----------

def ledger_lines() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def runs_used() -> int:
    return len(ledger_lines())


def find_cached_run(params_d: dict, half: str) -> dict | None:
    for row in ledger_lines():
        if row["half"] == half and row["params"] == params_d:
            return row
    return None


def summarize_run(r) -> dict:
    stops = [t for t in r.trade_log if t.exit_kind == "stop"]
    stop1 = [t for t in stops if t.bars_held <= 1]
    by_day: dict[str, list[int]] = {}
    for t in stops:
        by_day.setdefault(t.exit_date, []).append(t.pnl_krw)
    multi_days = {d for d, v in by_day.items() if len(v) >= 3}
    stop_pnl = sum(t.pnl_krw for t in stops)
    multi_pnl = sum(p for d, v in by_day.items() if d in multi_days for p in v)
    return {
        "ret": r.total_return, "mdd": r.mdd, "sharpe": r.sharpe,
        "trades": r.trades,
        "win": (r.wins / r.trades) if r.trades else 0.0,
        "stop_share": (len(stops) / len(r.trade_log)) if r.trade_log else 0.0,
        "stop1_share": (len(stop1) / len(stops)) if stops else 0.0,
        "multi_day_stop_pnl_share": (multi_pnl / stop_pnl) if stop_pnl else 0.0,
        "stop_pnl_krw": stop_pnl,
    }


def run_half(params: BaselineParams, half: str, half_data) -> dict:
    """원장 조회 → 없으면 실행(예산 1 소모) 후 기록."""
    params_d = asdict(params)
    cached = find_cached_run(params_d, half)
    if cached:
        return cached["stats"]
    used = runs_used()
    if used >= RUN_CAP:
        raise SystemExit(f"[budget] 상한 {RUN_CAP}회 도달 — 실행 불가 (used={used})")
    r = run_backtest(half_data, INITIAL_CAPITAL_KRW, params)
    stats = summarize_run(r)
    row = {"n": used + 1, "half": half, "params": params_d, "stats": stats}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats


# ---------- 게이트 ----------

def load_v0_halves() -> tuple[dict, dict]:
    d = json.loads(COMPARE_JSON.read_text(encoding="utf-8"))
    return d["V0 기준|전반"], d["V0 기준|후반"]


def passes_gate(s1: dict, s2: dict, v01: dict, v02: dict) -> bool:
    return (s1["mdd"] < v01["mdd"] and s1["sharpe"] > v01["sharpe"]
            and s2["mdd"] < v02["mdd"] and s2["sharpe"] > v02["sharpe"])


def robust_key(s1: dict, s2: dict):
    """캐리포워드 기준: 나쁜 쪽 샤프 최대화, 동률이면 나쁜 쪽 MDD 최소화."""
    return (min(s1["sharpe"], s2["sharpe"]), -max(s1["mdd"], s2["mdd"]))


def log_experiment(label: str, s1: dict, s2: dict, verdict: str, lesson: str) -> None:
    EXPERIMENTS.parent.mkdir(parents=True, exist_ok=True)
    line = (f"{label} | 전반 {s1['ret']:+.1%}/{s1['mdd']:.1%}/{s1['sharpe']:+.2f}"
            f" | 후반 {s2['ret']:+.1%}/{s2['mdd']:.1%}/{s2['sharpe']:+.2f}"
            f" | {verdict} | {lesson}\n")
    with EXPERIMENTS.open("a", encoding="utf-8") as f:
        f.write(line)


def param_label(p: BaselineParams) -> str:
    parts = []
    if p.cooldown_bars:
        parts.append(f"cd={p.cooldown_bars}")
    if p.stop_vol_k is not None:
        parts.append(f"volk={p.stop_vol_k}")
    if getattr(p, "stop_vol_lookback", None) not in (None, 20):
        parts.append(f"lb={p.stop_vol_lookback}")
    if p.market_breadth_min is not None:
        parts.append(f"breadth={p.market_breadth_min}")
    if p.max_new_per_day is not None:
        parts.append(f"maxnew={p.max_new_per_day}")
    return ",".join(parts) or "V0(off)"


# ---------- verify ----------

def cmd_verify() -> int:
    data = load_cache()
    mid, h1, h2 = split_halves(data)
    print(f"[verify] 분할 기준일 {mid} (기존 compare: 2025-11-12과 일치해야 함)")
    v01, v02 = load_v0_halves()
    s1 = run_half(BaselineParams(), "전반", h1)
    s2 = run_half(BaselineParams(), "후반", h2)
    ok = True
    for name, mine, ref in (("전반", s1, v01), ("후반", s2, v02)):
        for k in ("ret", "mdd", "sharpe"):
            match = abs(mine[k] - ref[k]) < 1e-9
            ok &= match
            print(f"  {name} {k:7s}: cache={mine[k]:+.6f}  ref={ref[k]:+.6f}  "
                  f"{'OK' if match else 'MISMATCH'}")
        for k in ("trades",):
            match = mine[k] == ref[k]
            ok &= match
            print(f"  {name} {k:7s}: cache={mine[k]}  ref={ref[k]}  "
                  f"{'OK' if match else 'MISMATCH'}")
    print(f"[verify] {'통과 — 캐시가 원본 수집과 동일 결과' if ok else '실패 — 캐시 신뢰 불가'}")
    print(f"[budget] 사용 {runs_used()}/{RUN_CAP}")
    return 0 if ok else 1


# ---------- diagnose (실행 0회) ----------

def _daily_rets(closes: list[int], i: int, lookback: int) -> list[float] | None:
    """closes[:i+1] 기준 마지막 lookback개 일수익률 (데이터 부족 시 None)."""
    if i + 1 < lookback + 1:
        return None
    seg = closes[i - lookback: i + 1]
    if any(c <= 0 for c in seg):
        return None
    return [seg[j + 1] / seg[j] - 1 for j in range(lookback)]


def cmd_diagnose() -> int:
    out: dict = {}

    # --- (1) 미션 근거 수치 검증 (V0 전기간 trade_log) ---
    tl = json.loads(V0_FULL_JSON.read_text(encoding="utf-8"))["trade_log"]
    stops = [t for t in tl if t["exit_kind"] == "stop"]
    stop1 = [t for t in stops if t["bars_held"] <= 1]
    sym_cnt: dict[str, int] = {}
    for t in stops:
        sym_cnt[t["symbol"]] = sym_cnt.get(t["symbol"], 0) + 1
    rep_syms = {s for s, c in sym_cnt.items() if c >= 2}
    rep_trades = [t for t in stops if t["symbol"] in rep_syms]
    stop_pnl = sum(t["pnl_krw"] for t in stops)
    rep_pnl = sum(t["pnl_krw"] for t in rep_trades)
    by_day: dict[str, list[int]] = {}
    for t in stops:
        by_day.setdefault(t["exit_date"], []).append(t["pnl_krw"])
    multi_days = sorted(d for d, v in by_day.items() if len(v) >= 3)
    multi_pnl = sum(p for d in multi_days for p in by_day[d])
    out["claims"] = {
        "stop_total": len(stops),
        "one_bar_share": len(stop1) / len(stops),
        "repeat_sym_trade_share": len(rep_trades) / len(stops),
        "repeat_sym_pnl_share": rep_pnl / stop_pnl,
        "multi_stop_days(>=3)": len(multi_days),
        "multi_day_pnl_share": multi_pnl / stop_pnl,
    }
    print("[diagnose] 미션 근거 수치 검증 (V0 645트레이드):")
    print(f"  stop {len(stops)}건 중 1봉 컷: {len(stop1)/len(stops):.0%} (미션: 58%)")
    print(f"  반복 손절 종목(2회+) 트레이드 비중: {len(rep_trades)/len(stops):.0%},"
          f" 손실액 비중: {rep_pnl/stop_pnl:.0%} (미션: 82%)")
    print(f"  동시 손절일(하루 3건+): {len(multi_days)}일,"
          f" stop 손실 비중: {multi_pnl/stop_pnl:.0%} (미션: 37일/52%)")

    # --- (2) σ20 국면 지연 분석 (캐시 기반) ---
    data = load_cache()
    closes_by_sym = {s: [b.close for b in bs] for s, bs in data.items()}
    dates_by_sym = {s: [b.date for b in bs] for s, bs in data.items()}
    all_dates = sorted({d for ds in dates_by_sym.values() for d in ds})
    date_idx = {s: {d: i for i, d in enumerate(ds)} for s, ds in dates_by_sym.items()}

    med_sig20, med_inst, floor_bind, cap_bind, dates_used = [], [], [], [], []
    for d in all_dates:
        sig20s, insts = [], []
        for s, closes in closes_by_sym.items():
            i = date_idx[s].get(d)
            if i is None:
                continue
            r20 = _daily_rets(closes, i, 20)
            r5 = _daily_rets(closes, i, 5)
            if r20 is None or r5 is None:
                continue
            sig20s.append(statistics.pstdev(r20))
            insts.append(statistics.pstdev(r5))
        if len(sig20s) >= 50:
            dates_used.append(d)
            med_sig20.append(statistics.median(sig20s))
            med_inst.append(statistics.median(insts))
            dists = [2.0 * x for x in sig20s]  # V2: k=2.0
            floor_bind.append(sum(1 for x in dists if x < 0.02) / len(dists))
            cap_bind.append(sum(1 for x in dists if x > 0.10) / len(dists))

    # 국면 전환 시점: 후반 시작(2025-11-13) 이후 σ5 중앙값이 처음으로
    # 전반 σ5 중앙값의 2배를 넘는 날 vs σ20이 같은 기준을 넘는 날 → 지연 일수
    mid = "2025-11-12"
    pre = [v for d, v in zip(dates_used, med_inst) if d <= mid]
    base = statistics.median(pre)
    thr = 2.0 * base

    def first_cross(series):
        for d, v in zip(dates_used, series):
            if d > mid and v >= thr:
                return d
        return None

    c_inst, c_sig = first_cross(med_inst), first_cross(med_sig20)
    lag_days = None
    if c_inst and c_sig:
        lag_days = dates_used.index(c_sig) - dates_used.index(c_inst)
    h2 = [i for i, d in enumerate(dates_used) if d > mid]
    h1 = [i for i, d in enumerate(dates_used) if d <= mid]
    out["sigma"] = {
        "baseline_sig5_median": base,
        "threshold(2x)": thr,
        "sig5_cross": c_inst, "sig20_cross": c_sig, "lag_trading_days": lag_days,
        "h1_med_sig20": statistics.median([med_sig20[i] for i in h1]),
        "h2_med_sig20": statistics.median([med_sig20[i] for i in h2]),
        "h1_med_sig5": statistics.median([med_inst[i] for i in h1]),
        "h2_med_sig5": statistics.median([med_inst[i] for i in h2]),
        "h1_floor_bind": statistics.median([floor_bind[i] for i in h1]),
        "h2_floor_bind": statistics.median([floor_bind[i] for i in h2]),
        "h1_cap_bind": statistics.median([cap_bind[i] for i in h1]),
        "h2_cap_bind": statistics.median([cap_bind[i] for i in h2]),
        "understate_ratio_h2": statistics.median(
            [med_inst[i] / med_sig20[i] for i in h2 if med_sig20[i] > 0]),
        "understate_ratio_h1": statistics.median(
            [med_inst[i] / med_sig20[i] for i in h1 if med_sig20[i] > 0]),
    }
    print("\n[diagnose] σ20 국면 지연 (유니버스 중앙값 기준):")
    print(f"  σ5(즉각 지표) 임계 돌파일: {c_inst} / σ20 돌파일: {c_sig}"
          f" → 지연 {lag_days}거래일" if lag_days is not None else
          f"  임계 돌파: σ5={c_inst}, σ20={c_sig} (한쪽 미돌파)")
    s = out["sigma"]
    print(f"  σ20 중앙값: 전반 {s['h1_med_sig20']:.2%} → 후반 {s['h2_med_sig20']:.2%}")
    print(f"  σ5  중앙값: 전반 {s['h1_med_sig5']:.2%} → 후반 {s['h2_med_sig5']:.2%}")
    print(f"  σ5/σ20 비율(과소추정도): 전반 {s['understate_ratio_h1']:.2f}"
          f" → 후반 {s['understate_ratio_h2']:.2f}")
    print(f"  V2 클램프(k=2.0): 하한 2% 바인딩 전반 {s['h1_floor_bind']:.0%}"
          f"/후반 {s['h2_floor_bind']:.0%},"
          f" 상한 10% 바인딩 전반 {s['h1_cap_bind']:.0%}/후반 {s['h2_cap_bind']:.0%}")

    DIAGNOSIS.parent.mkdir(parents=True, exist_ok=True)
    DIAGNOSIS.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[diagnose] 저장: {DIAGNOSIS} (백테스트 실행 0회)")
    return 0


# ---------- sweep ----------

def cmd_sweep() -> int:
    data = load_cache()
    _, h1, h2 = split_halves(data)
    v01, v02 = load_v0_halves()

    incumbent = BaselineParams()
    inc_stats = (v01, v02)  # V0 수치는 기존 compare JSON 재사용 (실행 0회)

    def eval_params(p: BaselineParams, phase: str):
        nonlocal incumbent, inc_stats
        s1 = run_half(p, "전반", h1)
        s2 = run_half(p, "후반", h2)
        gate = passes_gate(s1, s2, v01, v02)
        verdict = "채택후보" if gate else "기각"
        lesson = phase
        log_experiment(param_label(p), s1, s2, verdict, lesson)
        print(f"  {param_label(p):32s} 전반 {s1['ret']:+7.1%}/{s1['mdd']:5.1%}/{s1['sharpe']:+5.2f}"
              f"  후반 {s2['ret']:+7.1%}/{s2['mdd']:5.1%}/{s2['sharpe']:+5.2f}"
              f"  {'GATE✓' if gate else 'gate✗'}")
        if robust_key(s1, s2) > robust_key(*inc_stats):
            incumbent, inc_stats = p, (s1, s2)
        return gate

    print(f"[sweep] V0 기준 — 전반 mdd {v01['mdd']:.1%}/샤프 {v01['sharpe']:+.2f},"
          f" 후반 mdd {v02['mdd']:.1%}/샤프 {v02['sharpe']:+.2f}")
    print(f"[budget] 시작 시점 사용량 {runs_used()}/{RUN_CAP}\n")

    print("[P2] cooldown_bars ∈ {3,5,10}")
    for cd in (3, 5, 10):
        eval_params(BaselineParams(cooldown_bars=cd), "P2 축")
    best_cd = incumbent.cooldown_bars
    print(f"  → 캐리포워드 cooldown={best_cd}\n")

    print("[P1] stop_vol_k ∈ {1.5,2.0,2.5}")
    for k in (1.5, 2.0, 2.5):
        eval_params(BaselineParams(cooldown_bars=best_cd, stop_vol_k=k), "P1 축")
    best_k = incumbent.stop_vol_k
    print(f"  → 캐리포워드 stop_vol_k={best_k}\n")

    print("[P3] market_breadth_min ∈ {0.3,0.4,0.5}")
    for m in (0.3, 0.4, 0.5):
        eval_params(BaselineParams(cooldown_bars=best_cd, stop_vol_k=best_k,
                                   market_breadth_min=m), "P3 축")
    best_m = incumbent.market_breadth_min
    print(f"  → 캐리포워드 breadth={best_m}\n")

    print("[P3'] max_new_per_day ∈ {2,3}")
    for n in (2, 3):
        eval_params(BaselineParams(cooldown_bars=best_cd, stop_vol_k=best_k,
                                   market_breadth_min=best_m, max_new_per_day=n),
                    "P3' 축")
    print(f"\n[sweep] 최종 캐리포워드: {param_label(incumbent)}")
    print(f"[budget] 사용 {runs_used()}/{RUN_CAP}")
    return 0


def cmd_ext(params_json: str) -> int:
    data = load_cache()
    _, h1, h2 = split_halves(data)
    v01, v02 = load_v0_halves()
    p = BaselineParams(**json.loads(params_json))
    s1 = run_half(p, "전반", h1)
    s2 = run_half(p, "후반", h2)
    gate = passes_gate(s1, s2, v01, v02)
    log_experiment(param_label(p), s1, s2, "채택후보" if gate else "기각", "확장")
    print(f"{param_label(p)}  전반 {s1['ret']:+7.1%}/{s1['mdd']:5.1%}/{s1['sharpe']:+5.2f}"
          f"  후반 {s2['ret']:+7.1%}/{s2['mdd']:5.1%}/{s2['sharpe']:+5.2f}"
          f"  {'GATE 통과' if gate else 'gate 미달'}")
    print(f"  1봉컷: 전반 {s1['stop1_share']:.0%}/후반 {s2['stop1_share']:.0%}"
          f"  동시손절 손실비중: 전반 {s1['multi_day_stop_pnl_share']:.0%}"
          f"/후반 {s2['multi_day_stop_pnl_share']:.0%}")
    print(f"[budget] 사용 {runs_used()}/{RUN_CAP}")
    return 0


# ---------- report ----------

def cmd_report() -> int:
    v01, v02 = load_v0_halves()
    rows = ledger_lines()
    by_params: dict[str, dict] = {}
    for row in rows:
        key = json.dumps(row["params"], sort_keys=True)
        by_params.setdefault(key, {"params": row["params"]})[row["half"]] = row["stats"]
    candidates, rejected = [], []
    for e in by_params.values():
        if "전반" not in e or "후반" not in e:
            continue
        p = BaselineParams(**e["params"])
        if asdict(p) == asdict(BaselineParams()):
            continue
        entry = (param_label(p), e["전반"], e["후반"])
        (candidates if passes_gate(e["전반"], e["후반"], v01, v02) else rejected).append(entry)
    candidates.sort(key=lambda x: robust_key(x[1], x[2]), reverse=True)

    diag = json.loads(DIAGNOSIS.read_text(encoding="utf-8")) \
        if DIAGNOSIS.exists() else {}

    def fmt_row(lbl, s1, s2):
        return (f"| {lbl} | {s1['ret']:+.1%} | {s1['mdd']:.1%} | {s1['sharpe']:+.2f}"
                f" | {s2['ret']:+.1%} | {s2['mdd']:.1%} | {s2['sharpe']:+.2f} |")

    lines = [
        "# 백테스트 미션 보고서", "",
        f"- 기간: {START}~{END}, 분할 2025-11-12, 게이트: 양쪽 구간 V0 대비 MDD↓ AND 샤프↑",
        f"- 백테스트 실행: {len(rows)}/{RUN_CAP}회 (원장: state/sweep_runs.jsonl)", "",
        "## 1. 채택 후보", "",
        "| 파라미터 | 전반 ret | 전반 MDD | 전반 샤프 | 후반 ret | 후반 MDD | 후반 샤프 |",
        "|---|---|---|---|---|---|---|",
        fmt_row("V0 기준", v01, v02),
    ]
    for lbl, s1, s2 in candidates[:3]:
        lines.append(fmt_row(f"**{lbl}**", s1, s2))
    if not candidates:
        lines.append("| (게이트 통과 조합 없음) | | | | | | |")
    lines += ["", "## 2. 기각 조합", ""]
    for lbl, s1, s2 in rejected:
        why = []
        if not (s1["mdd"] < v01["mdd"] and s1["sharpe"] > v01["sharpe"]):
            why.append("전반 미달")
        if not (s2["mdd"] < v02["mdd"] and s2["sharpe"] > v02["sharpe"]):
            why.append("후반 미달")
        lines.append(f"- {lbl}: {'/'.join(why)}"
                     f" (전반 {s1['mdd']:.1%}/{s1['sharpe']:+.2f},"
                     f" 후반 {s2['mdd']:.1%}/{s2['sharpe']:+.2f})")
    lines += ["", "## 3. V0 대비 실패 패턴 변화 (채택 후보)"]
    base1bar = diag.get("claims", {}).get("one_bar_share")
    for lbl, s1, s2 in candidates[:3]:
        lines.append(
            f"- {lbl}: stop 비중 전반 {s1['stop_share']:.0%}/후반 {s2['stop_share']:.0%}"
            f" (V0: {v01['stop_share']:.0%}/{v02['stop_share']:.0%}),"
            f" 1봉 컷 전반 {s1['stop1_share']:.0%}/후반 {s2['stop1_share']:.0%}"
            + (f" (V0 전기간: {base1bar:.0%})" if base1bar else "")
            + f", 동시손절일 손실비중 전반 {s1['multi_day_stop_pnl_share']:.0%}"
              f"/후반 {s2['multi_day_stop_pnl_share']:.0%}")
    lines += ["", "## 4. 진단 요약 (0단계)", ""]
    if diag:
        c, sg = diag["claims"], diag["sigma"]
        lines += [
            f"- 1봉 컷 {c['one_bar_share']:.0%} / 반복 종목 손실 비중 {c['repeat_sym_pnl_share']:.0%}"
            f" / 동시 손절일 {c['multi_stop_days(>=3)']}일에 {c['multi_day_pnl_share']:.0%} — 미션 근거 재확인",
            f"- σ20 국면 지연: σ5 돌파 {sg['sig5_cross']} vs σ20 돌파 {sg['sig20_cross']}"
            f" (지연 {sg['lag_trading_days']}거래일)",
            f"- σ5/σ20 과소추정비: 전반 {sg['understate_ratio_h1']:.2f} → 후반 {sg['understate_ratio_h2']:.2f}",
            f"- V2 클램프 바인딩(중앙값): 하한 전반 {sg['h1_floor_bind']:.0%}/후반 {sg['h2_floor_bind']:.0%},"
            f" 상한 전반 {sg['h1_cap_bind']:.0%}/후반 {sg['h2_cap_bind']:.0%}",
        ]
    lines += [
        "", "## 5. 남은 불확실성 / 다음 검증 제안", "",
        "- 단일 1년 구간(2025-07~2026-07) — 다른 1년 구간 재검증 미수행 (미검증)",
        "- 유니버스 생존 편향 일부 잔존 (start 시점 고정 선정) (미검증)",
        "- 채택 후보의 모의투자 실운용 검증 (미검증)",
    ]
    out = STATE_DIR / "backtest_mission_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] 저장: {out}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["cache", "verify", "diagnose", "sweep", "ext", "report"])
    p.add_argument("--params", help="ext: BaselineParams 필드 JSON")
    a = p.parse_args()
    if a.cmd == "cache":
        build_cache()
        return 0
    if a.cmd == "verify":
        return cmd_verify()
    if a.cmd == "diagnose":
        return cmd_diagnose()
    if a.cmd == "sweep":
        return cmd_sweep()
    if a.cmd == "ext":
        if not a.params:
            print("ext에는 --params가 필요합니다")
            return 1
        return cmd_ext(a.params)
    if a.cmd == "report":
        return cmd_report()
    return 1


if __name__ == "__main__":
    sys.exit(main())
