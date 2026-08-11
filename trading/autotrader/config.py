"""전역 설정. 안전 관련 수치는 여기 하드코딩 (런타임 변경 금지).

2026-07-19 사용자 확정값: 자본금 1,000만원, "표준" 리스크 프로파일.
비율은 basis point(1bp = 0.01%) 정수로 표기 — 부동소수점 오차 방지.
"""
from pathlib import Path

# 모의 초기 자본금 (원) — 백테스트 기준액
INITIAL_CAPITAL_KRW = 10_000_000

# --- 데스크 배분 (2026-07-22 사용자 확정) ---
# 총자본 1,000만원을 미장팀 75% / 스윙팀 25% 로 나눈다 (DESKS.md 가 단일 출처).
# KIS 모의계좌에는 1,000만원이 그대로 들어있고 줄일 수단이 없다. 그래서 계좌 잔고가
# 아니라 이 배분액을 기준으로 수량·리스크를 계산한다.
#
# ACCOUNT_BASELINE_KRW 가 필요한 이유: 데스크 지분은 고정이 아니라 벌면 늘어야 한다.
#   데스크 지분 = SWING_ALLOCATION + (현재 계좌 평가액 − 계좌 최초 잔고)
# 이 계좌는 스윙 외의 매매를 하지 않으므로 계좌 손익 전부가 스윙 손익이다.
# 배분액을 그냥 상한으로 쓰면 번 돈이 재투자되지 않아 복리가 죽는다.
SWING_ALLOCATION_KRW = 2_500_000
ACCOUNT_BASELINE_KRW = 10_000_000

# --- 리스크 한도 ---
DAILY_LOSS_LIMIT_BP = 300      # 일일 손실 한도: 계좌의 3%
MAX_POSITION_WEIGHT_BP = 1_500  # 종목당 최대 15%
MAX_POSITIONS = 8               # 동시 보유 최대 종목 수
MAX_TRADE_RISK_BP = 150         # 트레이드당 리스크(진입가↔손절가) 1.5%

# --- 상태 파일 ---
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
KILLSWITCH_FILE = STATE_DIR / "killswitch.json"
DAILY_BLOCK_FILE = STATE_DIR / "daily_block.json"
