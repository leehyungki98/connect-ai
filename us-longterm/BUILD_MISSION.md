# BUILD_MISSION — 미국주식 장기 투자 데스크 (모의 전용)

> 이 문서 하나로 데스크 전체를 빌드한다. 여기 없는 기능은 만들지 마라.
> 스윙 데스크(connect-ai/trading)의 철학을 복제한다: "안 죽는 구조를 먼저 만들고
> 그 위에서 공격한다", "모의로 검증 가능한 것만 지금 만든다".

## 0. 확정 파라미터 (사용자 승인 완료, 2026-07-20)

| 항목 | 결정 |
|---|---|
| 스윙 vs 장기 자금 비율 | 스윙 25 / 장기 75 → 장기 모의 자본 3,000만원 (스윙 1,000만원 기준 환산) |
| 코어 배분 | 지수 ETF 60% / 개별 성장주 30% / 현금(USD) 10% |
| 리밸런싱 | 분기 점검(1·4·7·10월 첫 거래일) + 목표 비중 ±5%p 밴드 이탈 자산만 |
| 데이터 소스 | yfinance (무료, 키 불필요, 일봉·환율·수정종가) |
| 벤치마크 | S&P500(SPY) 총수익 — USD 기준 판정, KRW 환산 병기 |
| 성장주 구성 | **NVDA / TSM / META 각 10%** (2026-07-20 데이터 검증 후 사용자 승인) |

파생 결정 (단순화, 위 결정에서 논리적으로 도출):
- 지수 ETF는 S&P500 추종 1종: **VOO** (모의라 SPY도 무방하나 벤치마크와 종목을 분리해 둔다).
- 모든 가격은 yfinance `auto_adjust=True` 수정종가 = **배당 재투자 가정**.
  벤치마크(SPY TR)와 동일 기준이라 공정 비교가 된다. 배당 현금 처리 로직은 만들지 않는다.
- 성장주 30% 슬리브: NVDA/TSM/META 각 10% 확정 (§0-1 편입 기준 통과 + 사용자 승인).
  편입 논지는 `ledger/reviews/2026-07-20_growth_thesis.md` 참조 — 이 파일은 이미
  작성되어 있으니 그대로 ledger에 유지하라.

### 0-1. 성장주 슬리브 편입 기준 (헌법 승격 — 분기 리뷰마다 같은 잣대 적용)

이 데스크는 손절이 없고 밴드 리밸런싱이 하락 종목을 기계적으로 추가 매수한다.
따라서 "회복 전제"가 숫자로 성립하는 종목만 편입한다:

1. 검증된 수익성: 순이익률 15%↑ + FCF 흑자
2. 성장: 매출 성장(YoY) +15%↑
3. 밸류에이션: 선행PER 30↓ 또는 PEG 1.5↓

판정 기록 (2026-07-17 종가 데이터): NVDA·TSM·META 3/3 통과 → 편입.
PLTR 2/3 (밸류 불합격, 선행PER 63/PEG 1.9) → **관찰 목록** — 분기 리뷰에서 재평가.
TSLA 0/3 (이익률 3.9%, 매출 +16%, 선행PER 149) → 편입 불가. 스토리 베팅은
이 구조의 영역이 아니다.

## 1. 절대 제약 (위반 시 빌드 실패)

1. **100% 모의.** 실거래 API·실주문·증권사 연결 일절 금지. 체결은 내부 페이퍼 엔진만.
2. **안전층은 결정적 코드.** `config.py`, `safety/`, `gates/`에는 LLM 호출·외부 판단이
   들어갈 수 없다. 이 세 곳은 빌드 완료 후 편집 금지 구역으로 선언된다.
3. **스윙팀 무접촉.** [2026-07-20 편입 개정 — INTEGRATION_DESIGN §2] connect-ai
   저장소 내 `us-longterm/` 폴더에서 만든다. **`trading/` 폴더는 읽기 외 접근 금지.**
   두 데스크 간 코드 공유·import·복붙 금지는 그대로. 루트 변경은 DESKS.md·.gitignore
   로 한정. state 는 데스크별 독립.
4. **ISA 로직 금지.** 데이터 모델에 버킷 차원만 두고, ISA 자리는 주석으로만 표시.
5. **"완료/검증됨" 보고 전에 실제 테스트 출력 첨부.** 안 돌린 건 "미검증" 명시.
6. **손절 없음.** 이 데스크의 안전 규칙은 손절이 아니라 ±5%p 밴드 리밸런싱 +
   버킷 리스크 한도 + 킬스위치다.

## 2. 폴더 구조

[2026-07-20 편입 개정] 루트는 `connect-ai/us-longterm/`.

```
us-longterm/
├── CLAUDE.md            # 헌법 (§8 초안을 그대로 사용)
├── README.md            # 운영 루틴 요약
├── longcore/
│   ├── config.py        # [편집 금지 구역] 모든 상수 하드코딩
│   ├── safety/          # [편집 금지 구역] killswitch.py, guard.py
│   ├── gates/           # [편집 금지 구역] rebalance_gate.py, paper_only.py
│   ├── data/            # yfinance 시세·환율 수집 + 로컬 캐시
│   ├── portfolio/       # 보유·NAV·비중·환노출 계산 (순수 함수)
│   ├── rebalance/       # 밴드 판정 + 주문 목록 생성 (순수 함수)
│   └── paper/           # 페이퍼 체결 엔진 (수수료 반영, 상태 갱신)
├── scripts/
│   ├── run_daily.py     # 일일 스냅샷: NAV·비중·환노출 기록, 밴드 이탈 플래그 (주문 없음)
│   ├── run_rebalance.py # 분기 점검일 실행: 게이트 통과 시에만 페이퍼 주문
│   ├── run_report.py    # 벤치마크 대비 성과 (USD/KRW)
│   └── run_backtest.py  # 리밸런싱 규칙 백테스트
├── state/               # git 제외 — 운영 데이터 (재생성 가능)
│   ├── holdings.json    # 버킷별 보유
│   ├── nav_log.jsonl    # 일일 NAV (USD/KRW)·비중·환노출
│   ├── fx_log.jsonl     # USDKRW 일일 기록
│   └── killswitch.json
├── ledger/              # git 추적 — 학습 자산 (재생성 불가)
│   ├── rebalances/      # 리밸런싱 실행 기록 (사유·전후 비중·주문)
│   └── reviews/         # 분기 리뷰
└── tests/               # 전부 오프라인 (네트워크 금지)
```

[2026-07-20 편입 개정] git init 없음 — connect-ai 저장소에 커밋. ignore 는 connect-ai
루트 `.gitignore`의 `us-longterm/state/`·`us-longterm/.env` (trading 관례와 동일,
`__pycache__/` 등은 루트에 이미 존재).

## 3. 데이터 모델 — 버킷 차원

```python
# config.py
BUCKETS = {
    "해외증권": {
        "currency": "USD",
        "capital_krw": 30_000_000,
        "targets": {          # 슬리브 목표 비중 (합 = 1.0)
            "ETF": 0.60,      # 구성 종목: {"VOO": 1.0}
            "GROWTH": 0.30,   # 구성 종목: {"NVDA": 1/3, "TSM": 1/3, "META": 1/3}
            "CASH": 0.10,
        },
        "band": 0.05,                 # ±5%p
        "max_single_stock": 0.10,     # 개별 성장주 1종목 ≤ 버킷 NAV의 10%
                                      # (리밸런싱 후 목표 상태에 적용. 시장 변동으로
                                      #  일시 초과하는 건 분기 점검에서 교정)
        "rebalance_months": [1, 4, 7, 10],
    },
    # [확장점] ISA 버킷은 여기 추가된다. ISA 로직(납입한도·세제·오버플로우
    # 자금배분 규칙 층)은 실전에서만 검증 가능하므로 지금은 구현하지 않는다.
    # 나중에 "입금 → ISA 한도까지 → 초과분 일반계좌" 규칙 층이 이 위에 얹힌다.
}
COMMISSION_BPS = 25          # 페이퍼 체결 수수료 0.25%
```

모든 state 파일은 버킷 이름으로 네임스페이스한다 (`holdings.json` 내부가
`{"해외증권": {...}}` 형태). 코드는 버킷 N개를 돌 수 있게 짜되, 지금은 1개만 존재.

## 4. 모듈 스펙

**data/** — yfinance로 일봉 수정종가(`auto_adjust=True`)와 USDKRW(`KRW=X`) 수집.
`state/`에 parquet/csv 캐시, 같은 날 재호출 시 캐시 사용. 실패 시 예외를 위로 전파
(조용한 기본값 금지 — fail-closed).

**portfolio/** — 순수 함수만: `nav(holdings, prices) -> USD`,
`weights(holdings, prices) -> {sleeve: pct}`,
`fx_exposure(holdings, prices) -> {"usd_exposure_pct": ..., "usdkrw": ..., "nav_krw": ...}`.
환노출 정의: 버킷 NAV 중 USD 표시 자산 비중(현금 포함 → 이 데스크는 사실상 100%.
그래서 **금액**과 **KRW 환산 NAV의 환율 기여분**을 함께 기록한다 — 환헤지는 안 하지만
리스크로 측정은 한다).

**rebalance/** — 순수 함수:
`check_bands(weights, targets, band) -> [이탈 슬리브]`,
`make_orders(holdings, prices, targets, 이탈slice) -> [주문]`.
규칙: 분기 점검일에 이탈 슬리브**만** 목표 비중으로 되돌린다. 이탈이 없으면 주문 0건.
성장주 슬리브 내부는 config의 종목별 목표 비중으로 배분하되 `max_single_stock` 상한 적용.

**paper/** — 주문 목록을 실행일 수정종가로 체결, 수수료 차감, holdings 갱신,
`ledger/rebalances/`에 전후 비중·사유 기록. 여기가 유일한 체결 경로다.

**safety/** — 스윙과 동일 패턴:
- `killswitch.py`: `engage(reason)/reset()/status()`, CLI(`python -m longcore.safety ...`).
  engaged면 모든 주문 차단. 상태 파일 손상 시 **fail-closed** (차단으로 간주).
- `guard.py`: 주문 실행 전 최종 점검 — 킬스위치, 현금 음수 방지, 단일 종목 상한.

**gates/** —
- `paper_only.py`: 주문 객체에 broker/실거래 흔적이 있으면 무조건 거부. 페이퍼 엔진
  외의 체결 경로 차단 (스윙의 컴플라이언스 게이트에 대응).
- `rebalance_gate.py`: (a) 오늘이 분기 점검일인가, (b) 밴드 이탈이 실제 있는가,
  (c) 주문이 이탈 슬리브만 건드리는가 — 셋 다 통과해야 체결 허용.

## 5. 스크립트 동작

- `run_daily.py`: 시세·환율 수집 → NAV/비중/환노출 계산 → `nav_log.jsonl`·`fx_log.jsonl`
  기록 → 밴드 이탈 시 콘솔 경고만 (주문 절대 없음).
- `run_rebalance.py`: 게이트 3종 통과 시에만 페이퍼 체결. `--dry-run` 지원(기본 권장).
- `run_report.py`: 데스크 NAV vs SPY TR — 동일 시점 투입 가정, USD 수익률로 판정,
  KRW 환산 수익률 병기, 차이 중 환율 기여분 분해 표시.
- `run_backtest.py`: 과거 구간(기본 2016-01~2025-12)에서 확정 구성(VOO 60 /
  NVDA·TSM·META 각 10 / 현금 10)으로 분기 ±5%p 규칙 시뮬레이션.
  산출: CAGR·MDD·샤프·vs SPY TR. 이 백테스트는 **리밸런싱 메커니즘 검증**용이다.
  ⚠ 성과 수치는 참고만 하라 — 오늘 고른 종목을 과거에 적용하는 것이므로
  사후 선택 편향이 있다. "과거에 이겼으니 검증됨"이라고 보고하지 마라.
  같은 데이터로 파라미터(밴드·주기) 반복 튜닝 금지.

## 6. 테스트 요구 (전부 오프라인, fixture 가격 사용)

- 밴드 판정: 이탈/비이탈 경계값 (정확히 ±5.0%p일 때 포함 여부 명시하고 테스트)
- 주문 생성: 이탈 슬리브만 건드리는지, 목표 복원 후 비중 재계산 검증
- 단일 종목 상한, 현금 음수 방지
- 킬스위치: engage 시 주문 차단, 상태 파일 손상 시 fail-closed
- paper_only 게이트: 실거래 흔적 주문 거부
- 환노출·NAV 계산 (USD/KRW)
- 버킷 네임스페이스: 가상의 두 번째 버킷을 넣어도 계산이 분리되는지 (ISA 자리 검증 —
  단, ISA 로직이 아니라 버킷 분리만)

## 7. 완료 기준 (Definition of Done)

1. `pytest tests/ -q` 전체 통과 — 출력 첨부
2. `run_daily.py` 실제 1회 실행 (yfinance 실호출) — 출력 첨부
3. `run_backtest.py` 실행 결과 첨부 (지표 포함)
4. `run_rebalance.py --dry-run` 실행 출력 첨부
5. 위 4개 중 못 돌린 것은 "미검증"으로 명시
6. [2026-07-20 편입 개정] `trading/` 무변경 + 루트 변경이 DESKS.md·.gitignore 2건뿐임을
   `git status`/`git diff --stat` 출력으로 확인
7. 완료 보고에 편집 금지 구역 3곳(config.py, safety/, gates/) 경로 명시

## 8. CLAUDE.md 초안 (이 내용으로 생성하라)

```markdown
# Long-term_trader — 프로젝트 규칙 (모의투자 전용)

## 절대 규칙
- 편집 금지 구역: `longcore/safety/`, `longcore/gates/`, `longcore/config.py`.
  안전층(킬스위치·버킷 리스크 한도·리밸런싱 게이트)은 100% 결정적 코드 — LLM 호출 금지.
- 실거래 API 연결 금지. 체결은 내부 페이퍼 엔진만 (paper_only 게이트가 강제).
- 손절 없음. 안전 규칙 = 목표 비중 ±5%p 밴드 리밸런싱(분기) + 킬스위치.
- 통화(환율) 노출은 리스크로 항상 기록·측정한다 (fx_log, nav_log의 KRW 병기).
- `state/`는 운영 데이터(커밋 금지), `ledger/`는 학습 자산(git 추적).
- 스윙 데스크(connect-ai/)는 별도 조직 — 읽기만 허용, 수정 금지.
- "완료/검증됨" 보고 전에 실제 테스트 출력으로 뒷받침. 안 돌렸으면 "미검증" 명시.
- 전략·파라미터 변경은 백테스트 검증 + 사용자 승인 후에만. 같은 데이터 반복 최적화 금지.
- ISA: 버킷 차원만 존재. ISA 로직(한도·세제·오버플로우 배분)은 실전 검증 불가 —
  구현 금지, config.py의 확장점 주석 참조.

## 확정 파라미터 (2026-07-20 사용자 승인)
- 자금: 스윙 25 / 장기 75 (장기 모의 자본 3,000만원)
- 배분: ETF(VOO) 60 / 성장주(NVDA·TSM·META 각 10) 30 / 현금(USD) 10
- 성장주 편입 기준 (분기 리뷰 잣대): ① 순이익률 15%↑ + FCF 흑자
  ② 매출 성장 +15%↑ ③ 선행PER 30↓ 또는 PEG 1.5↓ — 셋 다 통과해야 편입.
  교체는 분기 리뷰에서만, 사용자 승인 필수, 논지는 ledger/reviews/에 기록.
- 관찰 목록: PLTR (사업 합격, 밸류 불합격 — 분기마다 재판정). TSLA 편입 불가 판정
  (2026-07-20, 3개 기준 전부 미달 — 재론하려면 숫자가 기준 안으로 들어와야 한다).
- 리밸런싱: 분기(1·4·7·10월 첫 거래일) + ±5%p 밴드 이탈 슬리브만
- 벤치마크: SPY 총수익 (USD 판정, KRW 병기)
- 데이터: yfinance 수정종가 (배당 재투자 가정)

## 운용 명령
- 매일: `python scripts/run_daily.py`
- 분기 점검일: `python scripts/run_rebalance.py --dry-run` → 확인 후 실행
- 성과: `python scripts/run_report.py`
- 테스트: `python -m pytest tests/ -q`
- 킬스위치: `python -m longcore.safety status|engage "이유"|reset`
```

## 9. 명시적 비범위 (만들지 마라)

- ISA 로직 전부 (한도·세제·오버플로우 자금배분 층)
- 실거래·브로커 연결, KIS API 포함
- 손절/모멘텀/타이밍 전략, LLM 기반 판단 로직 (종목 선정 프로세스는 코드 밖 절차)
- 배당 현금 처리 (수정종가 가정으로 대체)
- 스윙 데스크와의 코드 공유·import
- 웹 UI, 알림, 스케줄러 (운용은 수동 스크립트 실행)
