"""거래일별 주문 멱등성 원장 (모의 전용).

같은 거래일에 프리마켓이 두 번 돌면 같은 주문이 두 번 나갈 수 있다.
scripts/run_premarket.py 의 당일 재실행 가드는 "완주한 실행"만 막으므로,
주문을 몇 건 낸 뒤 죽은 실행이나 --force/--resume 재실행은 가드를 통과한다.

이 원장은 주문 단위로 한 겹 더 막는다. 식별자는 date-symbol-side 로 고정하고,
주문 API 호출 *전에* 기록한다 — 호출 후에 기록하면 그 사이의 크래시가 곧
중복 주문이 되기 때문이다. 같은 이유로 결과를 못 받은(sending) 항목도
"이미 시도됨"으로 본다 (fail-closed). 거래소에 닿지 못한 실패(failed)만
재시도를 허용한다.

파일 손상 시: 무엇이 나갔는지 알 수 없으므로 LedgerCorrupted 를 올린다.
호출부는 fail-closed 로 주문 전체를 막는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path


class LedgerCorrupted(RuntimeError):
    """원장 파일을 읽을 수 없음 — 중복 여부 판정 불가."""


class OrderLedger:
    """path=None 이면 비활성 (기록·조회 모두 무동작)."""

    def __init__(self, path: Path | None, today: date):
        self.path = path
        self.today = today
        self.entries: dict[str, dict] = {}
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stamp = str(raw["date"])
            orders = raw["orders"]
            if stamp == today.isoformat():  # 지난 거래일 원장은 버린다
                self.entries = {str(k): dict(v) for k, v in orders.items()}
        except (json.JSONDecodeError, KeyError, TypeError,
                ValueError, AttributeError, OSError) as e:
            raise LedgerCorrupted(str(e)) from e

    @staticmethod
    def make_key(today: date, symbol: str, side: str) -> str:
        return f"{today.isoformat()}-{symbol}-{side}"

    def attempted(self, symbol: str, side: str) -> dict | None:
        """이미 시도된 주문이면 그 기록. failed 는 미시도로 본다."""
        e = self.entries.get(self.make_key(self.today, symbol, side))
        if e is None or e.get("status") == "failed":
            return None
        return e

    def mark_sending(self, symbol: str, side: str, **detail) -> None:
        if self.path is None:
            return
        self.entries[self.make_key(self.today, symbol, side)] = {
            "status": "sending", **detail,
        }
        self._flush()

    def mark_result(self, symbol: str, side: str, success: bool, message: str = "") -> None:
        if self.path is None:
            return
        e = self.entries.get(self.make_key(self.today, symbol, side))
        if e is None:
            return
        e["status"] = "placed" if success else "failed"
        e["message"] = message
        self._flush()

    def _flush(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"date": self.today.isoformat(), "orders": self.entries},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)
