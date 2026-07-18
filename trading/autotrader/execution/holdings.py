"""보유 메타 저장소: 종목별 손절가/목표가/보유기간 (수량은 KIS 잔고가 기준).

KIS 잔고에는 우리의 손절/목표 정보가 없으므로 여기 JSON으로 보관한다.
- reconcile: KIS 잔고와 대조해 실제 보유 중인 종목만 Holding으로 변환.
  잔고에 없는 메타는 제거(청산 완료), 메타 없는 보유는 unmanaged로 보고.
- 파일 손상 시 fail-closed: RuntimeError → 상위에서 매매 전체 중단.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from autotrader.execution.stop_monitor import Holding
from autotrader.gates.types import Position


@dataclass(frozen=True)
class HoldingMeta:
    stop_price: int
    target_price: int
    horizon_days: int
    entry_date: str  # ISO (YYYY-MM-DD)


class HoldingsStore:
    def __init__(self, path: Path):
        self._path = Path(path)

    def load(self) -> dict[str, HoldingMeta]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {
                sym: HoldingMeta(
                    int(m["stop_price"]), int(m["target_price"]),
                    int(m["horizon_days"]), str(m["entry_date"]),
                )
                for sym, m in raw.items()
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
            raise RuntimeError(f"holdings state corrupted (fail-closed): {e}")

    def save(self, metas: dict[str, HoldingMeta]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({s: asdict(m) for s, m in metas.items()}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)


def reconcile(
    metas: dict[str, HoldingMeta], positions: dict[str, Position]
) -> tuple[list[Holding], dict[str, HoldingMeta], list[str]]:
    """(관리 중 보유, 정리된 메타, 메타 없는 보유 종목) 반환."""
    holdings, pruned, unmanaged = [], {}, []
    for sym in sorted(positions):
        pos = positions[sym]
        if pos.qty <= 0:
            continue
        meta = metas.get(sym)
        if meta is None:
            unmanaged.append(sym)
            continue
        pruned[sym] = meta
        holdings.append(
            Holding(
                sym, pos.qty, date.fromisoformat(meta.entry_date),
                meta.stop_price, meta.target_price, meta.horizon_days,
            )
        )
    return holdings, pruned, unmanaged
