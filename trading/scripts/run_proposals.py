"""변경 제안 카드 심사 — 유일한 사용자 승인 지점의 CLI.

레오·코다리가 만든 카드는 pending 에 쌓이기만 한다. 이 스크립트가 사람이
승인/거부/수정지시를 내리는 입구다. 자동 승인은 없다.

사용법:
  python scripts/run_proposals.py list
  python scripts/run_proposals.py show <id>
  python scripts/run_proposals.py approve <id>
  python scripts/run_proposals.py reject <id> "사유"
  python scripts/run_proposals.py revise <id> "수정 지시"

id 는 뒤 6자리만 입력해도 된다 (폰에서 치기 쉽게).

승인하면 결정적 게이트가 순서대로 돈다:
  편집 금지 구역 재검사 → git apply --check → git apply → 전체 테스트
  → 실패 시 자동 롤백(git apply -R). 통과해야만 반영이 유지된다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from autotrader.config import STATE_DIR  # noqa: E402
from autotrader.proposals import ProposalCard, ProposalQueue  # noqa: E402


def make_queue() -> ProposalQueue:
    return ProposalQueue(
        base_dir=STATE_DIR / "proposals",
        repo_root=ROOT.parent,  # connect-ai 저장소 루트 (git apply 기준)
    )


def resolve(queue: ProposalQueue, arg: str) -> ProposalCard | None:
    """전체 id 우선, 없으면 뒤에서 일치하는 pending 카드를 찾는다.

    접미사가 여러 카드에 걸리면 None 을 주고 호출부가 목록을 보여준다 —
    폰에서 잘못 승인하는 사고를 막는다.
    """
    card = queue.get(arg)
    if card is not None:
        return card
    hits = [c for c in queue.list_pending() if c.id.endswith(arg)]
    return hits[0] if len(hits) == 1 else None


def short(card: ProposalCard) -> str:
    head = (card.proposal or "").splitlines()[0] if card.proposal else "(내용 없음)"
    return f"{card.id[-6:]}  [{card.status}]  {head[:70]}"


def cmd_list(queue: ProposalQueue) -> int:
    cards = queue.list_pending()
    if not cards:
        print("심사 대기 카드 없음.")
        return 0
    print(f"심사 대기 {len(cards)}건:")
    for c in cards:
        print("  " + short(c))
        if c.revisions:
            print(f"        (수정 왕복 {len(c.revisions)}회, 마지막: {c.revisions[-1][:60]})")
    print("\n자세히: show <id> / 승인: approve <id> / 거부: reject <id> \"사유\"")
    return 0


def cmd_show(queue: ProposalQueue, arg: str) -> int:
    card = resolve(queue, arg)
    if card is None:
        print(f"카드를 찾지 못했습니다: {arg}")
        return cmd_list(queue) or 2
    print(f"[{card.id}]  상태: {card.status}   생성: {card.created[:19]}")
    print("=" * 70)
    print("## 분석\n" + (card.analysis or "(없음)"))
    print("\n## 진단\n" + (card.diagnosis or "(없음)"))
    print("\n## 제안\n" + (card.proposal or "(없음)"))
    if card.revisions:
        print("\n## 수정 이력")
        for i, r in enumerate(card.revisions, 1):
            print(f"  {i}. {r}")
    if card.error:
        print(f"\n## 마지막 오류\n{card.error}")
    diff = card.diff or ""
    print(f"\n## diff ({len(diff.splitlines())}줄)")
    print(diff[:4000] + ("\n... (생략)" if len(diff) > 4000 else ""))
    return 0


def cmd_action(queue: ProposalQueue, action: str, arg: str, text: str) -> int:
    card = resolve(queue, arg)
    if card is None:
        print(f"카드를 찾지 못했거나 여러 건에 걸립니다: {arg}")
        return cmd_list(queue) or 2
    if action == "approve":
        print(f"[{card.id[-6:]}] 승인 — 게이트 실행 중 (금지구역 → git apply → 전체 테스트)...")
        r = queue.approve(card.id)
    elif action == "reject":
        r = queue.reject(card.id, text or "사유 미기재")
    else:
        if not text:
            print("수정 지시 내용을 함께 적어주세요.")
            return 2
        r = queue.request_revision(card.id, text)
    # r.ok 는 "액션 처리 성공"이라 needs_revision 으로 넘어가도 True 다.
    # 사용자에겐 승인이 실제로 반영됐는지가 중요하므로 상태로 판단한다.
    final = r.card.status if r.card is not None else ""
    good = final == "approved" if action == "approve" else r.ok
    print(f"{'✅' if good else '❌'} {r.message}")
    if r.card is not None:
        print(f"   상태: {r.card.status}")
    return 0 if r.ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["list", "show", "approve", "reject", "revise"])
    p.add_argument("id", nargs="?", default="")
    p.add_argument("text", nargs="*", help="reject 사유 / revise 지시")
    args = p.parse_args()

    queue = make_queue()
    if args.cmd == "list":
        return cmd_list(queue)
    if not args.id:
        print(f"{args.cmd} 에는 카드 id 가 필요합니다.")
        return cmd_list(queue) or 2
    if args.cmd == "show":
        return cmd_show(queue, args.id)
    return cmd_action(queue, args.cmd, args.id, " ".join(args.text).strip())


if __name__ == "__main__":
    raise SystemExit(main())
