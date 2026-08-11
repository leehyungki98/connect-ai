"""[편집 금지 구역] 킬스위치 — engaged 면 모든 체결 차단.

상태 파일 손상 시 fail-closed: 읽을 수 없으면 '차단'으로 간주한다.
파일이 아예 없는 것은 정상(한 번도 안 당김) — 차단 아님.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parents[2] / "state" / "killswitch.json"


def status() -> dict:
    if not STATE_FILE.exists():
        return {"engaged": False, "reason": None, "ts": None, "corrupt": False}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        engaged = data["engaged"]
        if not isinstance(engaged, bool):
            raise ValueError("engaged 가 bool 이 아님")
        return {
            "engaged": engaged,
            "reason": data.get("reason"),
            "ts": data.get("ts"),
            "corrupt": False,
        }
    except Exception:
        # fail-closed: 손상된 상태 파일 = 차단
        return {
            "engaged": True,
            "reason": "상태 파일 손상 — fail-closed 차단. 수동 reset 필요.",
            "ts": None,
            "corrupt": True,
        }


def engage(reason: str) -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "engaged": True,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return status()


def reset() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "engaged": False,
        "reason": None,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return status()
