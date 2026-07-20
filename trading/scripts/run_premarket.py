"""장 시작 전 1회 실행 (모의 전용).

사용법: python scripts/run_premarket.py [--proposer codex|claude] [--judge claude|codex] [--dry-run] [--force]
--dry-run: 유니버스/스크리너 상위 후보만 출력하고 브레인·주문은 건너뜀.
--force:   당일 재실행 가드를 무시하고 강제 실행.
필요: .env (KIS 키), pykrx (pip install pykrx), claude 또는 codex CLI 로그인.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 콘솔(cp949)에서 LLM 유래 문자(em-dash 등) 출력 크래시 방지.
# 인코딩 불가 문자는 대체 표기로 흘린다 (콘솔 인코딩은 유지 — 한글 가독성).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")


from autotrader.config import DAILY_BLOCK_FILE, KILLSWITCH_FILE, STATE_DIR
from autotrader.execution.holdings import HoldingsStore
from autotrader.kis.client import KISPaperClient
from autotrader.pipeline import run_premarket
from autotrader.safety.guard import SafetyGuard
from autotrader.safety.killswitch import KillSwitch
from autotrader.screener.ranking import rank
from autotrader.screener.universe import fetch_universe
from smoke_kis import load_env  # 같은 scripts/ 안의 .env 로더 재사용


def already_ran_today(log_path: Path, today: date) -> bool:
    """오늘 프리마켓이 이미 완주했는지. 완주 기록만 카운트한다.

    스케줄러 재발동·수동 재실행이 겹치면 같은 날 주문이 두 배로 나간다.
    중간에 실패한 실행은 로그를 남기지 않으므로, 로그 유무를 기준으로 하면
    "실패 후 재시도"는 정상적으로 통과한다.
    """
    if not log_path.exists():
        return False
    stamp = today.isoformat()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            if json.loads(line).get("date") == stamp:
                return True
        except json.JSONDecodeError:
            continue
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proposer", default="codex", choices=["codex", "claude"])
    p.add_argument("--judge", default="claude", choices=["claude", "codex"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="당일 재실행 가드 무시")
    args = p.parse_args()

    # 주문을 내는 실행만 막는다. --dry-run 은 주문이 없으므로 몇 번이든 허용.
    if not args.dry_run and not args.force:
        log_path = STATE_DIR / "premarket_log.jsonl"
        if already_ran_today(log_path, date.today()):
            print("[premarket] 오늘 이미 실행됨 — 중복 주문 방지로 건너뜁니다.")
            print("[premarket] 다시 돌리려면 --force 를 붙이세요.")
            return 0

    # pykrx용 KRX 로그인: .env의 KRX_ID/KRX_PW를 환경변수로 주입
    if (ROOT / ".env").exists():
        _env = load_env(ROOT / ".env")
        for _k in ("KRX_ID", "KRX_PW"):
            if _env.get(_k):
                os.environ.setdefault(_k, _env[_k])

    # --dry-run은 주문을 내지 않으므로 KIS 키 없이 동작한다.
    kis = None
    if not args.dry_run:
        env = load_env(ROOT / ".env")
        kis = KISPaperClient(env["KIS_APPKEY"], env["KIS_APPSECRET"], env["KIS_ACCOUNT"])

    print("[premarket] 유니버스 수집 중 (pykrx)...")
    universe = fetch_universe()
    print(f"[premarket] 유니버스 {len(universe)}종목")

    if args.dry_run:
        top = rank(universe, 10)
        print("[premarket] --dry-run: 스크리너 상위 후보만 출력")
        for r in top:
            print(f"  {r.symbol}: score={r.score:+.4f} ret20={r.ret20:+.1%}")
        return 0

    guard = SafetyGuard(KillSwitch(KILLSWITCH_FILE), DAILY_BLOCK_FILE)
    store = HoldingsStore(STATE_DIR / "holdings.json")
    report = run_premarket(
        kis, guard, store, universe, date.today(),
        STATE_DIR / "day_start.json",
        STATE_DIR / "cooldowns.json",
        proposer_brain=args.proposer, judge_brain=args.judge,
    )

    print(f"[premarket] blocked={report.blocked}")
    print(f"[premarket] brain_ok={report.brain_ok}")
    for x in report.exits_placed:
        print(f"  EXIT: {x}")
    for x in report.buys_placed:
        print(f"  BUY : {x}")
    for x in report.skipped:
        print(f"  SKIP: {x}")

    log = STATE_DIR / "premarket_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"ts": datetime.now().isoformat(), **report.__dict__},
            ensure_ascii=False,
        ) + "\n")
    return 0 if report.blocked is None else 2


if __name__ == "__main__":
    sys.exit(main())
