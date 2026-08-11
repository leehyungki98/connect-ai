"""KIS 모의투자 실연결 스모크 테스트 (주문 없음 — 토큰/시세/잔고만).

준비: 프로젝트 루트에 .env 파일 생성 (.env.example 참고)
사용법: python scripts/smoke_kis.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autotrader.kis.client import KISPaperClient


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def main():
    env_file = ROOT / ".env"
    if not env_file.exists():
        print(f"[smoke] .env 파일이 없습니다: {env_file}")
        print("        .env.example을 복사해 키를 채워주세요.")
        return 1
    env = load_env(env_file)
    missing = [k for k in ("KIS_APPKEY", "KIS_APPSECRET", "KIS_ACCOUNT") if not env.get(k)]
    if missing:
        print(f"[smoke] .env에 누락: {missing}")
        return 1

    c = KISPaperClient(env["KIS_APPKEY"], env["KIS_APPSECRET"], env["KIS_ACCOUNT"])

    print("[smoke] 1/3 토큰 발급...")
    c.ensure_token()
    print("        OK")

    print("[smoke] 2/3 현재가 조회 (005930 삼성전자)...")
    price = c.get_price("005930")
    print(f"        OK: {price:,}원")

    print("[smoke] 3/3 모의계좌 잔고 조회...")
    pf = c.get_portfolio()
    print(f"        OK: 총평가 {pf.equity_krw:,}원, 현금 {pf.cash_krw:,}원, "
          f"보유 {len(pf.positions)}종목")
    print("[smoke] 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
