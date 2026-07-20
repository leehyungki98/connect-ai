"""실시간 뷰어 렌더 — 순수 함수, 네트워크 없음."""
from longcore import live


def _state(points):
    return {
        "date": "2026-07-21", "updated": "2026-07-21 10:30 ET", "usdkrw": 1476.0,
        "cash_usd": 501.71, "total_usd": 5100.0,
        "total_pnl_usd": 40.0, "total_ret_pct": 0.8,
        "holdings": [{"ticker": "NVDA", "shares": 2.496, "price": 205.0,
                      "value_usd": 511.7, "pnl_usd": 5.4, "ret_pct": 1.07}],
        "points": points, "note": "",
    }


def test_renders_self_contained_html():
    html = live.render_html(_state([{"t": "10:00", "total_usd": 5060.0},
                                    {"t": "10:30", "total_usd": 5100.0}]))
    assert "<!doctype html>" in html.lower()
    assert "NVDA" in html
    assert "http-equiv=\"refresh\"" in html      # 자동 새로고침
    assert "5060" in html and "5100" in html     # 차트 데이터 인라인


def test_refresh_interval_embedded():
    html = live.render_html(_state([]), refresh_sec=30)
    assert 'content="30"' in html


def test_gain_uses_red_loss_blue():
    """한국 관례 — 이익 빨강, 손실 파랑."""
    up = live.render_html({**_state([]), "total_pnl_usd": 40.0})
    assert "#e5484d" in up
    down = live.render_html({**_state([]), "total_pnl_usd": -40.0})
    assert "#3b82f6" in down


def test_handles_empty_points():
    """점 0~1개여도 안 깨진다 (JS 가 '수집 중' 표시)."""
    html = live.render_html(_state([]))
    assert "수집 중" in html
