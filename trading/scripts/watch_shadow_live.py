"""스윙 섀도 포트폴리오 준실시간 뷰 — 브라우저에서 평가금액·손익을 본다.

순수 뷰어. 주문 0, 실매매·기록에 손 안 댐(shadow_state 읽기 + shadow_view.json·
live.html 만 씀). yfinance KR 티커(~15분 지연)로 보유 섀도를 평가한다.
카드용 shadow_view.json 도 매 폴링마다 갱신 → 대시보드가 그걸 읽어 평가금액을 띄운다.

사용: python scripts/watch_shadow_live.py            # 60초 간격
장 마감 중엔 마지막 종가로 평평(정상). Ctrl-C 종료.
"""
import argparse
import json
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader import shadow  # noqa: E402

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
LIVE_HTML = STATE_DIR / "shadow_live.html"
VIEW_JSON = STATE_DIR / "shadow_view.json"


def _yf_ticker(symbol: str) -> list:
    """KRX 6자리 → yfinance 후보 (.KS 코스피 / .KQ 코스닥). 둘 다 시도."""
    return [f"{symbol}.KS", f"{symbol}.KQ"]


def _prices(symbols) -> dict:
    """준실시간 종가 (원). yfinance 1분봉 마지막값."""
    import yfinance as yf
    out = {}
    for sym in symbols:
        for cand in _yf_ticker(sym):
            try:
                df = yf.Ticker(cand).history(period="2d", interval="1m")
                if not df.empty:
                    out[sym] = int(df["Close"].dropna().iloc[-1])
                    break
            except Exception:  # noqa: BLE001
                continue
    return out


def _build_view(state: dict, prices: dict) -> dict:
    rows, tot_val, tot_cost = [], 0, 0
    for sym, p in state.get("positions", {}).items():
        price = prices.get(sym, p["entry_price"])   # 못 받으면 진입가로 대체
        val = p["qty"] * price
        cost = p["qty"] * p["entry_price"]
        rows.append({
            "symbol": sym, "name": p.get("name", sym),
            "qty": p["qty"], "entry_price": p["entry_price"],
            "price": price, "value_krw": val, "cost_krw": cost,
            "pnl_krw": val - cost, "ret_pct": round((price / p["entry_price"] - 1) * 100, 2),
            "stop": p["stop"], "target": p["target"], "entry_date": p["entry_date"],
        })
        tot_val += val
        tot_cost += cost
    rows.sort(key=lambda r: -r["value_krw"])
    cash = state.get("cash_krw", 0)
    return {"positions": rows, "cash_krw": cash,
            "total_value_krw": tot_val + cash, "total_pnl_krw": tot_val - tot_cost,
            "total_ret_pct": round((tot_val - tot_cost) / tot_cost * 100, 2) if tot_cost else 0.0,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}


def _html(view: dict, refresh: int) -> str:
    up = view["total_pnl_krw"] >= 0
    col = "#e5484d" if up else "#3b82f6"
    sg = "+" if up else ""
    rows = ""
    for p in view["positions"]:
        c = "#e5484d" if p["pnl_krw"] >= 0 else "#3b82f6"
        s = "+" if p["pnl_krw"] >= 0 else ""
        rows += (f"<tr><td class='tk'>{p.get('name', p['symbol'])}<br><span style='opacity:.4;font-size:11px'>{p['symbol']}</span></td><td>{p['qty']}</td>"
                 f"<td>{p['price']:,}</td><td>{p['value_krw']:,}</td>"
                 f"<td style='color:{c}'>{s}{p['pnl_krw']:,}<br>{s}{p['ret_pct']}%</td></tr>")
    if not rows:
        rows = "<tr><td colspan='5' style='text-align:center;opacity:.5;padding:20px'>보유 섀도 없음 (프리마켓 돌면 진입 쌓임)</td></tr>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}"><title>스윙 섀도 (페이퍼)</title>
<style>body{{background:#0b0e14;color:#e6e9ef;font-family:system-ui,'Segoe UI',sans-serif;margin:0;padding:20px}}
.hd{{font-size:13px;opacity:.6}}.big{{font-size:32px;font-weight:700;margin:4px 0}}
.pnl{{font-size:16px;font-weight:600;color:{col}}}.sub{{font-size:12px;opacity:.55;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}
th,td{{text-align:right;padding:7px 8px;border-bottom:1px solid rgba(255,255,255,.07)}}
th{{opacity:.5;font-weight:500}}td.tk,th:first-child{{text-align:left;font-weight:700}}</style></head><body>
<div class="hd">🌓 스윙팀 섀도 포트폴리오 · 페이퍼 (주문 0 · yfinance ~15분 지연)</div>
<div class="big">₩{view['total_value_krw']:,}</div>
<div class="pnl">{sg}₩{view['total_pnl_krw']:,} ({sg}{view['total_ret_pct']}%)</div>
<div class="sub">{view['updated']} 갱신 · {refresh}초마다 새로고침 · 현금 ₩{view['cash_krw']:,}</div>
<table><thead><tr><th>종목</th><th>수량</th><th>현재가</th><th>평가액</th><th>평가손익</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    interval = max(15, args.interval)

    print(f"섀도 준실시간 관찰 — {interval}초 간격. 브라우저: {LIVE_HTML}")
    print("Ctrl-C 종료. (순수 뷰어 — 실매매·기록 무영향)")
    opened = False
    try:
        while True:
            state = shadow.load_state()
            syms = list(state.get("positions", {}))
            prices = _prices(syms) if syms else {}
            view = _build_view(state, prices)
            VIEW_JSON.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
            LIVE_HTML.write_text(_html(view, interval), encoding="utf-8")
            if not opened:
                webbrowser.open(LIVE_HTML.resolve().as_uri())
                opened = True
            print(f"  {view['updated']} · 보유 {len(syms)}종목 · "
                  f"평가 ₩{view['total_value_krw']:,} ({view['total_ret_pct']:+.2f}%)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n종료.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
