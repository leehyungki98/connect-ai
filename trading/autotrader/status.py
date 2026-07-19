"""게이트 상태판 데이터 소스. 100% 결정적 — state/gate_status.json 생성, UI가 읽는다.

내용: 킬스위치 상태, 당일 손실 한도 소진율, 당일 차단 여부, 오늘 게이트 거부 건수.
소진율 = 당일 손실 / 한도(시작 평가액의 3%). 0 미만(수익)은 0으로 클램프.
equity_now가 없으면(장중 갱신 전) used_pct는 null.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from autotrader.config import DAILY_LOSS_LIMIT_BP
from autotrader.safety.killswitch import KillSwitch


def build_gate_status(
    state_dir: Path,
    today: date,
    equity_now_krw: int | None = None,
) -> dict:
    state_dir = Path(state_dir)
    ks = KillSwitch(state_dir / "killswitch.json").state()

    day_start = None
    ds = state_dir / "day_start.json"
    if ds.exists():
        try:
            raw = json.loads(ds.read_text(encoding="utf-8"))
            if str(raw["date"]) == today.isoformat():
                day_start = int(raw["equity"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            pass

    daily_blocked = False
    blk = state_dir / "daily_block.json"
    if blk.exists():
        try:
            raw = json.loads(blk.read_text(encoding="utf-8"))
            daily_blocked = str(raw["date"]) == today.isoformat()
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            daily_blocked = True  # fail-closed 표시

    used_pct = None
    if day_start and day_start > 0 and equity_now_krw is not None:
        limit = day_start * DAILY_LOSS_LIMIT_BP // 10_000
        loss = day_start - equity_now_krw
        used_pct = max(0.0, round(loss / limit, 4)) if limit > 0 else None

    rejections = 0
    log = state_dir / "premarket_log.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date") == today.isoformat():
                rejections = len(rec.get("skipped", []))  # 마지막 실행 기준

    return {
        "date": today.isoformat(),
        "killswitch_engaged": ks.engaged,
        "killswitch_reason": ks.reason,
        "daily_blocked": daily_blocked,
        "day_start_equity_krw": day_start,
        "equity_now_krw": equity_now_krw,
        "daily_limit_used_pct": used_pct,   # 1.0 = 한도 100% 소진
        "gate_rejections_today": rejections,
    }


def write_gate_status(
    state_dir: Path, today: date, equity_now_krw: int | None = None
) -> Path:
    status = build_gate_status(state_dir, today, equity_now_krw)
    out = Path(state_dir) / "gate_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    return out
