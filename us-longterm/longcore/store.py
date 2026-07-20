"""state/ 파일 입출력 — 버킷 네임스페이스 (holdings.json 내부 {"해외증권": {...}}).

state/ 는 운영 데이터 (커밋 금지, 재생성 가능). ledger/ 기록은 paper.engine 담당.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = BASE_DIR / "state"
LEDGER_DIR = BASE_DIR / "ledger"
HOLDINGS_FILE = STATE_DIR / "holdings.json"


def load_holdings(path: Path = None) -> dict:
    p = path or HOLDINGS_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_holdings(holdings: dict, path: Path = None) -> None:
    p = path or HOLDINGS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(holdings, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def append_jsonl(filename: str, record: dict, state_dir: Path = None) -> None:
    d = state_dir or STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    with open(d / filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
