"""섀도 평가 1회 갱신 — 준실시간 KR 시세로 shadow_view.json 만 쓴다 (브라우저·루프 없음).

대시보드가 15분마다 이걸 백그라운드로 돌려 카드 평가금액을 최신으로 유지한다.
순수 뷰어 — 주문·기록·실매매 무영향 (shadow_state 읽기 + shadow_view.json 쓰기만).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from autotrader import shadow  # noqa: E402
from watch_shadow_live import VIEW_JSON, _build_view, _prices  # noqa: E402


def main() -> int:
    state = shadow.load_state()
    syms = list(state.get("positions", {}))
    prices = _prices(syms) if syms else {}
    view = _build_view(state, prices)
    VIEW_JSON.parent.mkdir(parents=True, exist_ok=True)
    VIEW_JSON.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
    print(f"섀도 평가 갱신 — 보유 {len(syms)}종목 · "
          f"₩{view['total_value_krw']:,} ({view['total_ret_pct']:+.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
