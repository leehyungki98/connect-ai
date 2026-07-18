"""최상위 안전 게이트. 모든 주문 경로는 SafetyGuard.check()를 통과해야 한다.

검사 순서: 킬스위치 → 당일 차단 기록 → 일일 손실 한도.
- 킬스위치: 수동 reset 전까지 영구 차단.
- 일일 손실 한도 위반: 당일(KST 거래일) 차단 기록을 남김.
  이후 손실이 회복돼도 그 날은 계속 차단. 날짜가 바뀌면 자동 해제.
- 차단 기록 파일 손상 시 fail-closed(당일 차단으로 간주).
절대규칙 2: 100% 결정적. LLM 호출 금지.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from autotrader.safety.daily_loss import check_daily_loss
from autotrader.safety.killswitch import KillSwitch
from autotrader.types import GateDecision


class SafetyGuard:
    def __init__(self, killswitch: KillSwitch, daily_block_file: Path):
        self._ks = killswitch
        self._block_file = Path(daily_block_file)

    def check(
        self, trading_date: date, equity_start_krw: int, equity_now_krw: int
    ) -> GateDecision:
        if self._ks.is_engaged():
            return GateDecision(
                False, f"killswitch engaged: {self._ks.state().reason}"
            )
        if self._blocked_on(trading_date):
            return GateDecision(False, "daily loss limit already breached today")
        res = check_daily_loss(equity_start_krw, equity_now_krw)
        if res.breached:
            self._block(trading_date, res.loss_krw, res.limit_krw)
            return GateDecision(
                False,
                f"daily loss limit breached: loss={res.loss_krw} "
                f"limit={res.limit_krw}",
            )
        return GateDecision(True, "ok")

    def _blocked_on(self, trading_date: date) -> bool:
        if not self._block_file.exists():
            return False
        try:
            raw = json.loads(self._block_file.read_text(encoding="utf-8"))
            return str(raw["date"]) == trading_date.isoformat()
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            return True  # fail-closed

    def _block(self, trading_date: date, loss_krw: int, limit_krw: int) -> None:
        self._block_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._block_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "date": trading_date.isoformat(),
                    "loss_krw": loss_krw,
                    "limit_krw": limit_krw,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(self._block_file)
