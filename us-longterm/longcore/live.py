"""실시간 관찰 뷰어 렌더 — 장중 평가금액 곡선을 자체 완결 HTML 로.

⚠ '보는 용도' 전용이다. 데스크 전략은 여전히 일봉 종가 기반이고, 이 모듈과
watch_live.py 는 nav_log/ledger/관찰 기록에 손대지 않는다 (읽기만). yfinance
무료 시세는 약 15분 지연 — 준실시간이다.

render_html 은 순수 함수 (네트워크 없음) — 테스트 가능. 데이터는 인라인되고
meta refresh 로 새로고침한다 (로컬 파일이라 fetch 는 CORS 로 막혀 meta 가 안전).
"""
import json


def render_html(state: dict, refresh_sec: int = 60) -> str:
    """state = {date, updated, cash_usd, usdkrw, cost_usd, holdings:[{ticker,shares,
    price,value_usd,pnl_usd,ret_pct}], points:[{t, total_usd}]} → 자체완결 HTML."""
    total_usd = state.get("total_usd", 0.0)
    total_krw = total_usd * state.get("usdkrw", 0.0)
    pnl_usd = state.get("total_pnl_usd", 0.0)
    ret = state.get("total_ret_pct", 0.0)
    up = pnl_usd >= 0
    color = "#e5484d" if up else "#3b82f6"          # 한국 관례: 이익 빨강 / 손실 파랑
    sign = "+" if pnl_usd >= 0 else ""

    rows = ""
    for h in state.get("holdings", []):
        c = "#e5484d" if h["pnl_usd"] >= 0 else "#3b82f6"
        s = "+" if h["pnl_usd"] >= 0 else ""
        rows += (
            f"<tr><td class='tk'>{h['ticker']}</td>"
            f"<td>{h['shares']:.3f}</td>"
            f"<td>${h['price']:,.2f}</td>"
            f"<td>${h['value_usd']:,.2f}</td>"
            f"<td style='color:{c}'>{s}${h['pnl_usd']:,.2f}<br>{s}{h['ret_pct']:.2f}%</td></tr>")

    pts = json.dumps([[p["t"], round(p["total_usd"], 2)] for p in state.get("points", [])])
    closed_note = state.get("note", "")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_sec}">
<title>미장팀 실시간 (모의)</title>
<style>
 body{{background:#0b0e14;color:#e6e9ef;font-family:system-ui,'Segoe UI',sans-serif;margin:0;padding:20px}}
 .hd{{font-size:13px;opacity:.6}}
 .big{{font-size:34px;font-weight:700;margin:4px 0}}
 .pnl{{font-size:16px;font-weight:600;color:{color}}}
 .sub{{font-size:12px;opacity:.55;margin-bottom:14px}}
 canvas{{width:100%;height:260px;background:#11151f;border-radius:10px}}
 table{{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}}
 th,td{{text-align:right;padding:7px 8px;border-bottom:1px solid rgba(255,255,255,.07)}}
 th{{opacity:.5;font-weight:500}} td.tk,th:first-child{{text-align:left;font-weight:700}}
</style></head><body>
 <div class="hd">🏛️ 미장팀 보유 · 준실시간 (모의 · yfinance ~15분 지연)</div>
 <div class="big">₩{total_krw:,.0f}</div>
 <div class="pnl">{sign}${pnl_usd:,.2f} ({sign}{ret:.2f}%)  ·  ${total_usd:,.2f}</div>
 <div class="sub">{state.get('updated','')} 갱신 · {refresh_sec}초마다 자동 새로고침 · 환율 {state.get('usdkrw',0):,.0f}{('  ·  ' + closed_note) if closed_note else ''}</div>
 <canvas id="c" width="900" height="260"></canvas>
 <table><thead><tr><th>종목</th><th>수량</th><th>현재가</th><th>평가액</th><th>평가손익</th></tr></thead>
 <tbody>{rows}</tbody></table>
<script>
 const P={pts};
 const cv=document.getElementById('c'),x=cv.getContext('2d');
 function draw(){{
   const W=cv.width,H=cv.height,pad=30;x.clearRect(0,0,W,H);
   if(P.length<2){{x.fillStyle='#667';x.font='13px sans-serif';x.fillText('데이터 수집 중… (장중에 점이 쌓입니다)',pad,H/2);return;}}
   const ys=P.map(p=>p[1]),mn=Math.min(...ys),mx=Math.max(...ys),rg=(mx-mn)||1;
   const X=i=>pad+i/(P.length-1)*(W-2*pad), Y=v=>pad+(1-(v-mn)/rg)*(H-2*pad);
   const upv=ys[ys.length-1]>=ys[0];x.strokeStyle=upv?'#e5484d':'#3b82f6';x.lineWidth=2;x.beginPath();
   P.forEach((p,i)=>{{i?x.lineTo(X(i),Y(p[1])):x.moveTo(X(i),Y(p[1]));}});x.stroke();
   x.fillStyle=upv?'#e5484d':'#3b82f6';const li=P.length-1;x.beginPath();x.arc(X(li),Y(ys[li]),3,0,7);x.fill();
   x.fillStyle='#667';x.font='11px sans-serif';x.fillText('$'+mx.toFixed(0),4,Y(mx)+4);x.fillText('$'+mn.toFixed(0),4,Y(mn)+4);
 }}
 draw();
</script></body></html>"""
