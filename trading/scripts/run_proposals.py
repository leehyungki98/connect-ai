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


FORBIDDEN = ("trading/autotrader/safety/", "trading/autotrader/gates/",
             "trading/autotrader/config.py")


def diff_facts(diff: str) -> dict:
    """diff 에서 사람이 판단에 쓸 사실만 결정적으로 뽑는다 (LLM 주장 아님)."""
    files, added, removed = [], 0, 0
    for line in (diff or "").splitlines():
        if line.startswith("diff --git a/"):
            files.append(line.split(" b/")[-1].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "files": files,
        "added": added,
        "removed": removed,
        "tests": [f for f in files if "/tests/" in f or f.endswith("_test.py")],
        "forbidden": [f for f in files if any(f.startswith(z) for z in FORBIDDEN)],
    }


def headline(card: ProposalCard) -> str:
    """제안문 첫 문장을 제목처럼 쓴다. 원문은 잘린 코드 경로로 시작해 읽기 어렵다."""
    if card.title.strip():
        return card.title.strip()[:90]
    text = " ".join((card.proposal or "").split())
    if not text:
        return "(내용 없음)"
    for stop in ("다. ", "한다. ", "정정: ", ". "):
        if stop in text[:200]:
            return text[: text.index(stop) + len(stop)].strip()[:90]
    return text[:90]


def short(card: ProposalCard) -> str:
    f = diff_facts(card.diff)
    scope = f"파일 {len(f['files'])}개 +{f['added']}/-{f['removed']}"
    flag = " ⚠️금지구역" if f["forbidden"] else (" ✅테스트포함" if f["tests"] else "")
    return f"{card.id[-6:]}  {headline(card)}\n        {scope}{flag}"


def cmd_list(queue: ProposalQueue) -> int:
    cards = queue.list_pending()
    if not cards:
        print("심사 대기 카드 없음.")
        return 0
    print(f"📋 심사 대기 {len(cards)}건 — 전략을 바꾸는 결정은 여기서만 이뤄집니다\n")
    for i, c in enumerate(cards, 1):
        f = diff_facts(c.diff)
        flag = "⚠️ 금지구역" if f["forbidden"] else ("테스트 포함" if f["tests"] else "테스트 없음")
        print(f"{i}) [{c.id[-6:]}] {headline(c)}")
        print(f"     범위: {len(f['files'])}개 파일 · +{f['added']}/-{f['removed']}줄 · {flag}")
        if c.revisions:
            print(f"     수정 왕복 {len(c.revisions)}회 — 마지막: {c.revisions[-1][:60]}")
        print()
    print("자세히: `show <id>`  (id 는 뒤 6자리)")
    print("승인 `approve <id>` / 거부 `reject <id> \"사유\"` / 수정요청 `revise <id> \"지시\"`")
    return 0


def _wrap(text: str, width: int = 300) -> str:
    """긴 단락을 앞부분만. 폰에서 스크롤 지옥이 되지 않게."""
    t = " ".join((text or "").split())
    return t if len(t) <= width else t[:width].rstrip() + " …"


def cmd_show(queue: ProposalQueue, arg: str, full: bool = False) -> int:
    card = resolve(queue, arg)
    if card is None:
        print(f"카드를 찾지 못했습니다: {arg}")
        return cmd_list(queue) or 2
    f = diff_facts(card.diff)

    print(f"📋 카드 {card.id[-6:]}   [{card.status}]")
    print(f"   {headline(card)}")
    print("=" * 64)
    print("■ 무엇이 문제인가")
    print(f"  {_wrap(card.diagnosis)}")
    print("\n■ 어떻게 바꾸나")
    print(f"  {_wrap(card.proposal, 400)}")
    print("\n■ 근거가 된 관찰")
    print(f"  {_wrap(card.analysis)}")

    if card.risk.strip():
        print("\n■ 승인하지 않으면 남는 위험")
        print(f"  {_wrap(card.risk)}")
    if card.tradeoff.strip():
        print("\n■ 승인했을 때의 단점")
        print(f"  {_wrap(card.tradeoff)}")
    if not (card.risk.strip() or card.tradeoff.strip()):
        print("\n■ 위험·단점: 이 카드에는 기록되지 않음 (구버전 카드)")

    print("\n■ 변경 범위 (diff 에서 직접 계산 — 모델 주장 아님)")
    for path in f["files"]:
        print(f"  · {path}")
    print(f"  총 +{f['added']}줄 / -{f['removed']}줄")
    print(f"  테스트 포함: {'예 (' + ', '.join(f['tests']) + ')' if f['tests'] else '아니오'}")
    print(f"  편집 금지 구역: {'⚠️ ' + ', '.join(f['forbidden']) if f['forbidden'] else '없음'}")

    print("\n■ 승인하면 벌어지는 일")
    print("  금지구역 재검사 → git apply → 전체 테스트 실행")
    print("  테스트 실패 시 자동 롤백(git apply -R). 통과해야만 반영이 유지된다.")
    print("  되돌리려면: git revert 또는 반대 diff 카드로 재제출.")

    if card.revisions:
        print("\n■ 수정 이력")
        for i, r in enumerate(card.revisions, 1):
            print(f"  {i}. {_wrap(r, 150)}")
    if card.error:
        print(f"\n■ 마지막 오류\n  {_wrap(card.error, 200)}")

    diff = card.diff or ""
    if full:
        print(f"\n■ diff 전문 ({len(diff.splitlines())}줄)")
        print(diff)
    else:
        print(f"\n■ diff {len(diff.splitlines())}줄 — 전문은 `show {card.id[-6:]} --full`")
    print(f"\n승인: approve {card.id[-6:]}   거부: reject {card.id[-6:]} \"사유\"")
    print(f"수정 요청: revise {card.id[-6:]} \"이렇게 고쳐줘\"")
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
    p.add_argument("--full", action="store_true", help="show: diff 전문 출력")
    args = p.parse_args()

    queue = make_queue()
    if args.cmd == "list":
        return cmd_list(queue)
    if not args.id:
        print(f"{args.cmd} 에는 카드 id 가 필요합니다.")
        return cmd_list(queue) or 2
    if args.cmd == "show":
        return cmd_show(queue, args.id, full=args.full)
    return cmd_action(queue, args.cmd, args.id, " ".join(args.text).strip())


if __name__ == "__main__":
    raise SystemExit(main())
