"""전역 설정. 안전 관련 수치는 여기 하드코딩 (런타임 변경 금지).

2026-07-19 사용자 확정값: 자본금 1,000만원, "표준" 리스크 프로파일.
비율은 basis point(1bp = 0.01%) 정수로 표기 — 부동소수점 오차 방지.
"""
from pathlib import Path

# 모의 초기 자본금 (원)
INITIAL_CAPITAL_KRW = 10_000_000

# --- 리스크 한도 ---
DAILY_LOSS_LIMIT_BP = 300      # 일일 손실 한도: 계좌의 3%
MAX_POSITION_WEIGHT_BP = 1_500  # 종목당 최대 15%
MAX_POSITIONS = 8               # 동시 보유 최대 종목 수
MAX_TRADE_RISK_BP = 150         # 트레이드당 리스크(진입가↔손절가) 1.5%

# --- 상태 파일 ---
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
KILLSWITCH_FILE = STATE_DIR / "killswitch.json"
DAILY_BLOCK_FILE = STATE_DIR / "daily_block.json"
