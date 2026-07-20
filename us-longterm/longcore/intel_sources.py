"""정세 소스 필터링 & 정규화 — RSS 수집·중복제거·관련도 판정.

이 모듈은 네트워크 호출 없이 순수 텍스트 처리만 한다.
- match_relevance: 뉴스 제목+요약에서 관심 티커/매크로 키워드 추출
- to_staging_record: RSS 아이템을 정규화된 dict로
- dedup: 기존 URL 중복 제거

## 커버리지 유니버스 — 보유 종목만이 아니다 (2026-07-21)
사용자 지적: "지금 내가 살 것만 위주로 가져오면 안 된다. 나중에 어떻게
리밸런싱할지 모르니까." 지금 안 사는 종목도 미래 편입 후보가 될 수 있고,
그때 정세 이력이 없으면 판단 근거가 없다. 스냅샷 설계와 같은 원칙 —
보유만 저장하면 "그때 PLTR 을 넣었어야 했나"를 영원히 답할 수 없다.

3계층으로 잡는다 (tier 는 classify_tier 가 config 에서 판정):
  held      — 현재 보유 (config BUCKETS GROWTH)
  watchlist — 관찰·기각 (config SNAPSHOT_WATCHLIST)
  candidate — 편입 후보군: NVDA/TSM/META 와 같은 판(대형 성장주·AI·반도체·
              메가캡 테크)에서 3기준(수익성·성장·밸류)을 언젠가 통과할 수 있는
              이름들. 판이 바뀌면 여기 갱신 — 편집 자유.
MACRO 태그가 종목 무관 매크로·지정학 뉴스를 넓게 받아 '판 전체' 를 커버한다.
"""
import re

# 티커 → 별칭(소문자). 단어 경계로 매칭하므로 짧은 티커도 안전.
# 계층은 config 로 판정하되(단일 출처), 별칭은 여기서 관리한다.
UNIVERSE = {
    # ── 보유 (config GROWTH) ──
    "NVDA": ["nvidia", "nvda"],
    "TSM": ["tsmc", "taiwan semiconductor", "tsm"],
    "META": ["meta", "facebook", "instagram", "whatsapp", "zuckerberg"],
    # ── 관찰·기각 (config SNAPSHOT_WATCHLIST) ──
    "PLTR": ["palantir", "pltr"],
    "TSLA": ["tesla", "tsla", "elon musk"],
    # ── 후보군: 반도체 (NVDA/TSM 와 같은 판) ──
    "AMD": ["advanced micro", "amd"],
    "AVGO": ["broadcom", "avgo"],
    "ASML": ["asml"],
    "MU": ["micron"],
    "QCOM": ["qualcomm"],
    "ARM": ["arm holdings"],           # 'arm' 단독은 warm/farm 오탐 → 전체명만
    "AMAT": ["applied materials"],
    # ── 후보군: 메가캡 테크 (META 와 같은 판) ──
    "MSFT": ["microsoft"],
    "GOOGL": ["alphabet", "google"],
    "AMZN": ["amazon"],
    "AAPL": ["apple"],
    # ── 후보군: AI 인프라·성장 ──
    "ANET": ["arista"],
    "SMCI": ["super micro", "supermicro"],
    "NOW": ["servicenow"],             # 'now' 단독은 흔한 단어 → 전체명만
    "CRM": ["salesforce"],
    "NFLX": ["netflix"],
}

# 종목 무관 매크로·지정학 — '판 전체' 커버 (미래 리밸런싱 대비).
MACRO_KEYWORDS = [
    "taiwan", "china", "semiconductor", "semiconductors", "chip", "chips",
    "gpu", "gpus", "data center", "data centers", "cloud computing",
    "export control", "export controls", "artificial intelligence", "ai",
    "federal reserve", "interest rate", "interest rates", "rate cut",
    "inflation", "antitrust", "tariff", "tariffs",
]

# 신흥 테마 — 대장주가 확정 안 됐고 계속 바뀐다 (2026-07-21, 사용자 요청: 수소 필수).
# 키워드로 '판 자체' 를 잡고(새 대장주가 떠도 안 놓침) + 현재 대표주도 등록한다.
# ⚠ 이 테마주 대부분은 지금 3기준(수익성·성장·밸류)을 통과 못 한다(적자·초기매출).
#    정세 추적은 '기록'이지 매수 신호가 아니다 — TSLA 를 기각하고도 관찰하는 것과 같다.
#    테마가 성숙하거나(예: BE 는 FCF 흑자 전환) 데스크 방향이 바뀔 때 이력이 있게 한다.
# 새 테마 추가는 여기 한 줄. 매칭되면 테마명 태그(예 "HYDROGEN")가 붙는다.
THEMES = {
    "HYDROGEN": {
        "keywords": ["hydrogen", "fuel cell", "fuel-cell", "electrolyzer",
                     "green ammonia"],
        "tickers": {"PLUG": ["plug power"], "BE": ["bloom energy"],
                    "FCEL": ["fuelcell"]},
    },
    "QUANTUM": {
        "keywords": ["quantum computing", "quantum computer", "qubit",
                     "quantum annealing"],
        "tickers": {"IONQ": ["ionq"], "RGTI": ["rigetti"],
                    "QBTS": ["d-wave", "dwave"]},
    },
}

# 테마 대표주를 UNIVERSE 에 합류 (티커 매칭용). 계층은 classify_tier 가 "theme" 로.
THEME_TICKERS = {t: a for spec in THEMES.values()
                 for t, a in spec["tickers"].items()}
for _t, _a in THEME_TICKERS.items():
    UNIVERSE.setdefault(_t, _a)


def _has_word(text: str, term: str) -> bool:
    """단어 경계 매칭 — 부분 문자열 오탐 방지 ('now'≠'right now', 'amd'≠'named').
    term 은 소문자. 공백 포함 구(phrase)도 경계로 감싼다."""
    return re.search(r"\b" + re.escape(term) + r"\b", text) is not None


def match_relevance(title: str, summary: str) -> list:
    """제목+요약에서 관심 티커/키워드 찾기.

    반환:
      - 매칭된 티커들 (예: ["NVDA", "AMD"])
      - 티커 무관하게 매크로 키워드가 걸리면 "MACRO" 포함
      - 아무것도 안 걸리면 빈 리스트 (무관 뉴스, 버린다)
    """
    text = (title + " " + summary).lower()
    matched = set()

    for ticker, aliases in UNIVERSE.items():
        if any(_has_word(text, a) for a in aliases):
            matched.add(ticker)

    # 신흥 테마 — 키워드가 걸리면 테마명 태그 (종목 특정 안 돼도 수집)
    for theme, spec in THEMES.items():
        if any(_has_word(text, k) for k in spec["keywords"]):
            matched.add(theme)

    if any(_has_word(text, k) for k in MACRO_KEYWORDS):
        matched.add("MACRO")

    return sorted(matched)


def classify_tier(ticker: str) -> str:
    """티커의 계층 — held / watchlist / candidate. config 가 단일 출처.
    config import 는 함수 안에서(순수 로직 모듈이 config 에 상시 의존하지 않게)."""
    if ticker == "MACRO":
        return "macro"
    if ticker in THEMES or ticker in THEME_TICKERS:
        return "theme"      # 신흥 테마 태그(HYDROGEN) 또는 대표주(PLUG) — 투기적, 3기준 미통과 다수
    try:
        from .config import BUCKETS, SNAPSHOT_WATCHLIST
    except Exception:
        return "candidate"
    held = set()
    for cfg in BUCKETS.values():
        held |= set(cfg.get("sleeves", {}).get("GROWTH", {}))
    if ticker in held:
        return "held"
    # 벤치마크 ETF(SPY 등)는 계층에서 제외 — 개별주 판정 대상 아님
    watch = {t for t in SNAPSHOT_WATCHLIST if t not in ("SPY", "VOO")}
    if ticker in watch:
        return "watchlist"
    return "candidate"


def to_staging_record(section: str, title: str, url: str, published: str,
                      summary: str, matched: list) -> dict:
    """RSS 아이템을 정규화된 staging dict로 변환.

    status="pending" 인 dict. 사람이 리뷰하면 "recorded" 또는 "skipped" 로 갱신.
    tiers: 매칭된 티커별 계층(held/watchlist/candidate/macro) — 리뷰 우선순위용.
    """
    return {
        "section": section,
        "title": title,
        "url": url,
        "published": published,
        "summary": summary,
        "matched": matched,
        "tiers": {t: classify_tier(t) for t in matched},
        "status": "pending",
    }


def dedup(records: list, seen_urls: set) -> list:
    """기존 URL 집합에 없는 레코드만 반환 (중복 제거)."""
    return [r for r in records if r["url"] not in seen_urls]
