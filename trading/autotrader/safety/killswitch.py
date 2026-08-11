"""킬스위치: 걸리면 모든 매매 차단.

절대규칙 2: 100% 결정적. LLM 호출 금지.
- 상태는 JSON 파일에 저장, 프로세스 재시작 후에도 유지.
- 파일 손상/읽기 실패 시 fail-closed(걸린 것으로 간주).
- 해제는 수동 reset() 뿐. 자동 해제 경로 없음.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class KillSwitchState:
    engaged: bool
    reason: str
    engaged_at: str  # ISO8601 UTC. engaged=False면 ""


class KillSwitch:
    def __init__(self, state_file: Path):
        self._file = Path(state_file)

    def state(self) -> KillSwitchState:
        if not self._file.exists():
            return KillSwitchState(False, "", "")
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            return KillSwitchState(
                bool(raw["engaged"]), str(raw["reason"]), str(raw["engaged_at"])
            )
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            return KillSwitchState(True, "state file corrupted (fail-closed)", "")

    def is_engaged(self) -> bool:
        return self.state().engaged

    def engage(self, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._write({"engaged": True, "reason": reason, "engaged_at": now})

    def reset(self) -> None:
        """수동 해제 전용."""
        self._write({"engaged": False, "reason": "", "engaged_at": ""})

    def _write(self, data: dict) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._file)
