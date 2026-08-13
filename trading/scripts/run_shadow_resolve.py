"""섀도 청산 판정 — 보유 중 섀도 포지션을 최신 일봉으로 손절/목표/기간 청산.

주문 없음. pykrx 일봉으로 백테스트와 동일 잣대(손절→목표→기간)로 판정하고
매도 시각·사유·실현손익을 shadow_trades.jsonl 에 남긴다.
매일(또는 장 마감 후) 1회 돌리면 섀도 포지션이 실매매처럼 청산된다.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autotrader import shadow  # noqa: E402


def _load_krx_env() -> None:
    """pykrx용 KRX 로그인 — 프리마켓과 동일하게 .env 의 KRX_ID/KRX_PW 주입."""
    from smoke_kis import load_env
    if (ROOT / ".env").exists():
        env = load_env(ROOT / ".env")
        for k in ("KRX_ID", "KRX_PW"):
            if env.get(k):
                os.environ.setdefault(k, env[k])


def _fetch_bars(symbol: str, from_date: str, to_date: str) -> list:
    """pykrx 일봉 (진입일~오늘). 반환: [{date, open, high, low, volume}] 시간순."""
    from pykrx import stock
    fd, td = from_date.replace("-", ""), to_date.replace("-", "")
    df = stock.get_market_ohlcv(fd, td, symbol)
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": int(row["시가"]), "high": int(row["고가"]),
            "low": int(row["저가"]), "volume": int(row["거래량"]),
        })
    return bars


def _resolve_book(book: str, bars_by_symbol: dict) -> None:
    """한 책(강세/약세)의 체결·청산 판정 + 사람 말 보고."""
    label = "강세책" if book == "bull" else "약세책"
    st0 = shadow.load_state(book)
    if not st0.get("positions") and not st0.get("pending"):
        return
    settled = shadow.settle_pending(bars_by_symbol, book=book)
    for e in settled:
        if e["event"] == "entry":
            print(f"  [{label}] 체결 {e.get('name', e['symbol'])} · {e['qty']}주 @ {e['entry_price']:,}원")
        else:
            print(f"  [{label}] 미체결 {e.get('name', e['symbol'])} · 실전에서도 안 샀을 주문")
    closed = shadow.resolve(bars_by_symbol, book=book)
    if closed:
        print(f"\n🔔 [{label}] 오늘 팔린 종목")
        for c in closed:
            win = c["ret_pct"] >= 0
            why = {"목표달성": "목표가 도달", "갭손절": "갭하락으로 손절",
                   "1봉손절": "하루 만에 손절", "손절": "손절",
                   "기간만료": "보유기간 만료"}.get(c["failure_kind"], c["failure_kind"])
            print(f"  {'📈' if win else '📉'} {c.get('name', c['symbol'])} — {why} "
                  f"{c['entry_price']:,}→{c['exit_price']:,} ({c['ret_pct']:+.2f}%) "
                  f"실현 {c['realized_krw']:+,}원")
    st = shadow.load_state(book)
    print(f"  [{label}] 남은 보유 {len(st.get('positions', {}))} · 대기 {len(st.get('pending', []))}")


def main() -> int:
    from datetime import date
    today = date.today().isoformat()
    # 두 책 + 샘플의 모든 보유·대기 종목을 모아 일봉 조회 대상으로.
    targets = set()
    for book in shadow.BOOKS:
        st = shadow.load_state(book)
        targets |= {(o["symbol"], o["order_date"]) for o in st.get("pending", [])}
        targets |= {(sym, p["entry_date"]) for sym, p in st.get("positions", {}).items()}
    samples = [r for r in shadow.load_samples()
               if r.get("status") in ("pending", "filled")]
    targets |= {(r["symbol"], r.get("entry_date") or r["date"]) for r in samples}
    if not targets:
        print("보유·대기 섀도 없음 — 판정할 것 없음.")
        return 0

    _load_krx_env()
    print(f"조회 대상 {len(targets)}건 → 일봉 조회")
    bars_by_symbol = {}
    for sym, since in targets:
        try:
            bars = _fetch_bars(sym, since, today)
            bars_by_symbol.setdefault(sym, [])
            have = {b["date"] for b in bars_by_symbol[sym]}
            bars_by_symbol[sym] += [b for b in bars if b["date"] not in have]
            bars_by_symbol[sym].sort(key=lambda b: b["date"])
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: 일봉 조회 실패 — 유지 ({e})")
            bars_by_symbol.setdefault(sym, [])

    # 강세책·약세책 각각 판정
    for book in shadow.BOOKS:
        _resolve_book(book, bars_by_symbol)

    # ③ 샘플 판정 — 계좌와 동일 로직, 제약만 없다 (폭 구간별 표본용)
    stat = shadow.resolve_samples(bars_by_symbol)
    if any(stat.values()):
        print(f"[샘플] 체결 {stat['filled']} · 미체결 {stat['unfilled']} · "
              f"청산 {stat['closed']}")
    all_s = shadow.load_samples()
    done = [r for r in all_s if r.get("status") in ("closed", "unfilled")]
    print(f"[샘플] 누적 {len(all_s)}건 · 판정완료 {len(done)}건 "
          f"(폭 구간별 분석은 run_shadow_report.py)")

    # ④ 카드 종가 스냅샷 — 대시보드 15분 갱신은 패널을 열어둬야 돌고, 마지막 장중 틱이
    #    15:30 이전 아무데나 떨어져 종가를 놓친다. 스케줄로 도는 여기서 확정한다.
    try:
        import run_shadow_refresh
        sys.argv = [sys.argv[0], "--close"]
        run_shadow_refresh.main()
    except Exception as e:  # noqa: BLE001
        print(f"  카드 종가 스냅샷 실패 — 표시 전용이라 무시 ({e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
