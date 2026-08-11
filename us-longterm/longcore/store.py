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


# nav 시계열은 재생성 불가 관찰 자산이다 — state/ 만 두면 폴더 삭제·디스크 사고로
# 영구 손실된다 (과거 시점 NAV 를 사후 재구성하면 그건 관찰이 아니다). 그래서
# git 추적되는 ledger/observations/ 로 미러링해 커밋 대상으로 만든다.
# state/nav_log.jsonl 은 계속 append 원본으로 두고, 여기서는 (date,bucket) 당
# 한 줄만 유지한다 — 하루에 여러 번 실행해도 중복이 쌓이지 않도록 멱등하게.
OBSERVATIONS_DIR = LEDGER_DIR / "observations"


def mirror_nav_record(record: dict, ledger_dir: Path = None) -> Path:
    """nav 기록 1건을 ledger 미러에 멱등 반영. 같은 (date, bucket) 은 덮어쓴다."""
    d = ledger_dir or OBSERVATIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / "nav_log.jsonl"

    key = (record.get("date"), record.get("bucket"))
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (r.get("date"), r.get("bucket")) == key:
                continue  # 같은 날·버킷의 옛 값은 버리고 새 값으로 교체
            rows.append(r)
    rows.append(record)
    # 날짜·버킷 순으로 정렬해 파일이 결정적이도록 (diff 가 깔끔해진다)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("bucket", "")))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path
