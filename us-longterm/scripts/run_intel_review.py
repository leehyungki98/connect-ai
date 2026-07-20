"""정세 항목 인터랙티브 리뷰 — 사람이 점수 + 근거 입력.

실행: python scripts/run_intel_review.py

로직:
  1. state/intel_staging/{현재분기}.jsonl 에서 pending 항목 로드
  2. 각 항목을 하나씩 보여주고:
     - ticker (예: NVDA, TSM, META, 또는 빈 입력 = 건너뛰기)
     - impact (-2 ~ +2 정수)
     - thesis_axis (수익성/성장/밸류/지정학/규제/경쟁 중 하나)
     - basis (근거, 필수)
  3. 입력받으면 intel.record() 호출 → ledger/intel/ 에 기록
  4. 처리한 항목 status를 "recorded" 또는 "skipped"로 갱신
  5. jsonl 다시 저장 (멱등: 다시 실행해도 recorded/skipped는 안 보임)

Ctrl-C/빈 입력으로 중간 종료 가능, 지금까지 처리분은 저장됨.
"""
import json
import sys
from datetime import date
from pathlib import Path

# 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import intel
from longcore.store import STATE_DIR
from longcore.intel import quarter_of, THESIS_AXES


STAGING_DIR = STATE_DIR / "intel_staging"


def load_staging_records(quarter: str) -> list[dict]:
    """staging jsonl 에서 모든 레코드 로드."""
    path = STAGING_DIR / f"{quarter}.jsonl"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_staging_records(quarter: str, records: list[dict]) -> None:
    """staging jsonl 전체 다시 쓰기 (멱등)."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{quarter}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_valid_input(prompt: str, valid_values: list[str] = None, allow_empty: bool = False) -> str:
    """사용자 입력 받기 & 검증.

    인자:
      prompt: 프롬프트 텍스트
      valid_values: 허용 값 리스트. None 이면 모두 허용
      allow_empty: 빈 입력 허용 여부

    반환:
      검증된 입력 문자열
    """
    while True:
        user_input = input(prompt).strip()
        if not user_input:
            if allow_empty:
                return ""
            print("  → 빈 입력은 불가. 다시 입력해주세요.")
            continue
        if valid_values is not None and user_input not in valid_values:
            print(f"  → 허용값: {', '.join(valid_values)}")
            continue
        return user_input


def main():
    """메인 리뷰 루프."""
    quarter = quarter_of(date.today())
    print(f"=== 정세 항목 리뷰 ({quarter}) ===\n")

    records = load_staging_records(quarter)
    print(f"로드: {len(records)} 건\n")

    # pending만 필터
    pending = [r for r in records if r.get("status") == "pending"]
    if not pending:
        print("리뷰할 pending 항목 없음.")
        return

    print(f"리뷰 대기: {len(pending)} 건\n")

    processed = 0
    try:
        for idx, record in enumerate(pending, 1):
            print(f"--- [{idx}/{len(pending)}] ---")
            print(f"제목: {record['title']}")
            print(f"섹션: {record['section']}")
            print(f"매칭: {', '.join(record['matched'])}")
            print(f"요약: {record['summary'][:150]}...")
            print(f"URL: {record['url']}\n")

            # ticker 입력
            ticker_input = get_valid_input(
                "티커 (NVDA/TSM/META, 빈 입력=건너뛰기): ",
                allow_empty=True
            )

            if not ticker_input:
                print("→ 건너뜀\n")
                record["status"] = "skipped"
                processed += 1
                continue

            # impact 입력
            impact_str = get_valid_input(
                "영향도 (-2 ~ +2): ",
                valid_values=[str(i) for i in range(-2, 3)]
            )
            impact = int(impact_str)

            # thesis_axis 입력
            axis_input = get_valid_input(
                f"논지축 ({'/'.join(THESIS_AXES)}): ",
                valid_values=list(THESIS_AXES)
            )

            # basis 입력
            basis = get_valid_input("근거 (한 줄): ")

            # record 적재
            try:
                intel.record(
                    ticker=ticker_input,
                    event_date=date.today(),
                    source=f"WSJ {record['section']}",
                    event=record["title"],
                    impact=impact,
                    thesis_axis=axis_input,
                    basis=basis,
                )
                print("→ 기록됨\n")
                record["status"] = "recorded"
                processed += 1
            except ValueError as e:
                print(f"→ 기록 실패: {e}")
                print("다시 시도해주세요.\n")

    except KeyboardInterrupt:
        print("\n\n중단됨 (Ctrl-C)")
    except EOFError:
        print("\n\n중단됨 (EOF)")

    # staging 갱신 (processed 항목들만)
    print(f"\n처리 완료: {processed} 건")
    save_staging_records(quarter, records)
    print(f"'{quarter}.jsonl' 갱신 완료\n")
    print("=== 리뷰 완료 ===")


if __name__ == "__main__":
    main()
