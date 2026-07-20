# us-longterm — 미장팀 · 미국주식 장기 데스크 (모의투자 전용)

실거래 불가 구조: 체결 경로가 내부 페이퍼 엔진뿐이고, paper_only 게이트가
브로커/실거래 흔적이 있는 주문을 무조건 거부한다. 브로커 SDK·API 키 자체가 없다.

포트폴리오 (모의 자본 3,000만원, KRW 환산):
VOO 60% / NVDA·TSM·META 각 10% / 현금(USD) 10%.
손절 없음 — 안전 규칙은 분기 ±5%p 밴드 리밸런싱 + 킬스위치.

## 운영 루틴

```
# 최초 1회 (보유가 비어 있을 때만) — 최초 배분
python scripts\run_rebalance.py --init

# 매일: NAV·비중·환노출 스냅샷 (주문 없음)
python scripts\run_daily.py

# 분기 점검일 (1·4·7·10월 첫 거래일): 밴드 이탈 슬리브만 목표 복원
python scripts\run_rebalance.py --dry-run     # 확인 후 --dry-run 제거

# 성과: vs SPY 총수익 (USD 판정, KRW 병기 + 환율 기여 분해)
python scripts\run_report.py

# 리밸런싱 규칙 백테스트 (참고용 — 사후 선택 편향 주의)
python scripts\run_backtest.py
```

## 킬스위치 (수동)

```
python -m longcore.safety status
python -m longcore.safety engage "이유"   # 모든 체결 차단 (수동 reset 전까지)
python -m longcore.safety reset
```

상태 파일 손상 시 fail-closed (차단으로 간주).

## 구조

- `longcore/config.py` · `longcore/safety/` · `longcore/gates/` — **편집 금지 구역**
  (100% 결정적 코드, LLM 금지)
- `longcore/{data,portfolio,rebalance,paper}` — 수집·계산(순수 함수)·페이퍼 체결
- `state/` — 운영 데이터 (커밋 금지) / `ledger/` — 학습 자산 (git 추적)
- 조직·권한: 루트 `DESKS.md` / 편입 기록: `INTEGRATION_DESIGN.md`

## 테스트

```
python -m pytest tests/ -q    # 전부 오프라인 (네트워크/키 불필요)
```
