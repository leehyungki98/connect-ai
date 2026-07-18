# autotrader_fable — 한국 주식 스윙 자동매매 (모의투자 전용)

실거래 불가 구조: 주문 도메인이 KIS 모의투자(`openapivts...:29443`) 상수로 하드코딩돼 있고,
컴플라이언스 게이트가 실전 도메인/TR ID를 무조건 거부한다.

## 일일 운영 루틴

```
# 장 시작 전 (킬스위치→잔고→청산→스크리너→LLM→게이트→주문)
python scripts\run_premarket.py            # --brain codex, --dry-run 옵션

# 마감 후 (평가액 스냅샷 + 선택적 LLM 사후분석)
python scripts\run_postmarket.py --review

# 백테스트 (결정적 코어만, LLM 제외)
python scripts\run_backtest.py             # --start/--end YYYYMMDD
```

## 킬스위치 (수동)

```
python -m autotrader.safety status
python -m autotrader.safety engage "이유"   # 모든 매매 차단 (수동 reset 전까지)
python -m autotrader.safety reset
```

일일 손실 -3% 도달 시 당일 자동 차단(다음 날 해제). 상태 파일 손상 시 fail-closed.

## 리스크 한도 (autotrader/config.py 하드코딩)

일일 손실 -3% / 종목당 15% / 최대 8종목 / 트레이드당 리스크 1.5% / 자본 1,000만원.
절대수익은 관찰만 한다 — 목표로 삼지 않는다.

## 준비물 (.env — 커밋 금지)

`KIS_APPKEY` `KIS_APPSECRET` `KIS_ACCOUNT`(모의투자), `KRX_ID` `KRX_PW`(pykrx 유니버스).
LLM 브레인은 로컬 `claude` CLI (또는 `codex`).

## 상태 파일 (state/)

`killswitch.json` `daily_block.json` `day_start.json` `holdings.json`(손절/목표 메타)
`premarket_log.jsonl` `equity_log.jsonl` `reviews/` `backtest_*.json`

## 테스트

```
python -m pytest tests/          # 163개 전부 오프라인 (네트워크/키 불필요)
```

## 알려진 한계

- 관리종목 필터 없음 (거래정지는 거래대금 0으로 자연 탈락)
- 백테스트 유니버스는 시작일 기준 고정 — 생존 편향 완전 제거 아님
- codex CLI는 전역 AGENTS.md 스킬 선택 설정과 충돌 (claude 기본 사용)
- KIS 잔고 응답의 보유종목 파싱은 실보유 발생 후 재확인 필요
