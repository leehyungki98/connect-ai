"""논지 재판정 — 편입 3기준을 분기마다 재적용해 퇴출 후보를 가려낸다.

설계 의도 (2026-07-20, 사용자 승인):
이 데스크는 손절이 없고 밴드 리밸런싱이 하락 종목을 기계적으로 추가 매수한다.
논지가 깨진 종목에도 물타기가 계속되는 게 원래 구조의 구멍이었다.
해법은 손절이 아니라 **논지 기반 강등**이다 — 값이 아니라 펀더멘털이 무너지면
개별주 베팅을 지수(VOO) 베팅으로 강등한다. 현금화가 아니므로 시장을 떠나지 않고,
따라서 타이밍 베팅이 아니다.

방아쇠는 하드 데이터(실적)만 당긴다. 정세분석가의 뉴스 점수는 이 판정에 들어오지
않는다 — 재현 불가능한 입력이 매매를 발동시키면 백테스트가 무의미해지기 때문.
정세 점수의 자리는 `ledger/intel/` 기록과 애매한 경우의 판단 재료다 (DESKS.md).

순수 함수 — 데이터 수집은 호출자 책임. 최종 실행은 항상 사용자 승인 뒤.
"""

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"   # 데이터 결측 — 미달로 치지 않는다 (fail-closed 아님:
                      # 결측을 퇴출 근거로 쓰면 데이터 사고가 매도로 이어진다)


def _num(v):
    """결측 정규화 — None 과 NaN 을 똑같이 '없음' 으로 만든다.

    NaN 을 걸러내지 않으면 `nan >= 0.15` 가 False 라서 **결측이 조용히 FAIL 이
    된다.** 2026-07-20 역사적 검증에서 실제로 잡힌 결함이다 (첫 회계연도처럼
    직전값이 없어 성장률이 NaN 인 구간이 전부 '성장 미달' 로 찍혔다).
    yfinance 는 float('nan') 을 그대로 돌려주므로 운영 경로에도 같은 구멍이 있었다.
    """
    if v is None:
        return None
    try:
        return None if v != v else float(v)   # NaN != NaN
    except TypeError:
        return None


def judge_criteria(fundamentals: dict, criteria: dict) -> dict:
    """종목 하나의 3기준 판정. fundamentals 키가 없거나 None 이면 UNKNOWN.

    반환: {"수익성": PASS/FAIL/UNKNOWN, "성장": ..., "밸류": ...}
    """
    out = {}

    margin = _num(fundamentals.get("net_margin"))
    fcf = _num(fundamentals.get("free_cashflow"))
    if margin is None or fcf is None:
        out["수익성"] = UNKNOWN
    else:
        out["수익성"] = (PASS if margin >= criteria["net_margin_min"] and fcf > 0
                       else FAIL)

    growth = _num(fundamentals.get("revenue_growth"))
    out["성장"] = (UNKNOWN if growth is None
                 else PASS if growth >= criteria["revenue_growth_min"] else FAIL)

    fpe = _num(fundamentals.get("forward_pe"))
    peg = _num(fundamentals.get("peg"))
    if fpe is None and peg is None:
        out["밸류"] = UNKNOWN
    else:
        # 둘 중 하나만 통과해도 합격 (편입 기준 ③ 원문 그대로)
        ok = ((fpe is not None and 0 < fpe <= criteria["forward_pe_max"])
              or (peg is not None and 0 < peg <= criteria["peg_max"]))
        out["밸류"] = PASS if ok else FAIL


    return out


def quarter_verdict(criteria_result: dict, fail_threshold: int) -> bool:
    """이번 분기 '미달'인가 — FAIL 개수가 임계 이상. UNKNOWN 은 세지 않는다."""
    return sum(1 for v in criteria_result.values() if v == FAIL) >= fail_threshold


def exit_candidates(history: dict, consecutive: int) -> list:
    """퇴출 후보 = 최근 N분기 연속 '미달'인 종목.

    history: {티커: [분기별 미달여부(bool)]} — 시간순, 마지막이 최신.
    일시적 실적 부진 한 분기로는 나가지 않는다.
    """
    out = []
    for ticker, verdicts in sorted(history.items()):
        recent = verdicts[-consecutive:]
        if len(recent) >= consecutive and all(recent):
            out.append(ticker)
    return out


def apply_exits(sleeves: dict, exits, exit_target: str) -> dict:
    """퇴출 종목을 성장주 슬리브에서 제거하고 그 비중을 남은 종목에 재분배.

    성장주가 전부 퇴출되면 GROWTH 슬리브는 비고, 호출자가 목표 비중을
    exit_target(VOO) 로 흡수시킨다 — `absorb_targets` 참조.
    """
    exits = set(exits)
    out = {s: dict(comp) for s, comp in sleeves.items()}
    growth = out.get("GROWTH", {})
    survivors = {t: w for t, w in growth.items() if t not in exits}
    if survivors:
        total = sum(survivors.values())
        out["GROWTH"] = {t: w / total for t, w in survivors.items()}
    else:
        out["GROWTH"] = {}
    return out


def absorb_targets(targets: dict, sleeves: dict) -> dict:
    """구성 종목이 0개가 된 슬리브의 목표 비중을 ETF 로 넘긴다."""
    out = dict(targets)
    moved = 0.0
    for sleeve, comp in sleeves.items():
        if sleeve in out and not comp and out[sleeve] > 0:
            moved += out[sleeve]
            out[sleeve] = 0.0
    if moved:
        out["ETF"] = out.get("ETF", 0.0) + moved
    return out
