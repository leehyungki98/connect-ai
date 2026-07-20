"""주간 백업 push 헬퍼 — 미push 커밋을 팀별로 보여주고 확인 후 올린다.

배경 (2026-07-21):
- 이 저장소는 trading/(스윙팀) 과 us-longterm/(미장팀) 을 한 개의 git 저장소에
  담는다. git push 는 디렉토리별로 못 쪼갠다 — 브랜치 커밋 전체가 원자적으로
  올라간다. 그래서 "팀 따로 push" 는 push 단위가 아니라 **미리보기 단위**다:
  올라갈 커밋을 팀별로 보여주고, 확인 후 한 번에 push 한다.
- 자동 push 가 아니다. 스윙팀 run_improve.py 도 "push 는 사람 판단" 을 명시한다.
  주간 사후분석 리포트를 받으면 이 스크립트를 한 번 돌리는 게 그 사람 판단이다.

사용:
  python scripts/push_backup.py           # 미리보기 후 y/n 확인
  python scripts/push_backup.py --yes      # 확인 없이 바로 push (스케줄러용)
  python scripts/push_backup.py --dry-run  # 미리보기만, push 안 함
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 커밋이 건드린 파일 경로로 소속 팀을 판정한다. 첫 매칭 우선.
TEAM_RULES = [
    ("스윙팀", "trading/"),
    ("미장팀", "us-longterm/"),
]


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def current_branch():
    _, out, _ = git("rev-parse", "--abbrev-ref", "HEAD")
    return out


def upstream(branch):
    code, out, _ = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    return out if code == 0 else None


def classify(files):
    teams = set()
    for f in files:
        for name, prefix in TEAM_RULES:
            if f.startswith(prefix):
                teams.add(name)
                break
        else:
            teams.add("공용/루트")
    return teams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="확인 없이 push")
    ap.add_argument("--dry-run", action="store_true", help="미리보기만")
    args = ap.parse_args()

    branch = current_branch()
    up = upstream(branch)
    if up is None:
        print(f"⚠ 브랜치 '{branch}' 에 upstream 이 없다.")
        print(f"  최초 1회: git push -u origin {branch}")
        return 1

    code, out, _ = git("rev-list", "--count", f"{up}..HEAD")
    n = int(out or "0")
    if n == 0:
        print(f"✓ 이미 최신 — '{branch}' 는 {up} 와 동기화됨. push 할 것 없음.")
        return 0

    # 미push 커밋을 팀별로 집계
    _, log, _ = git("log", "--format=%h%x00%s", f"{up}..HEAD")
    by_team = {}
    for line in log.splitlines():
        sha, subj = line.split("\x00", 1)
        _, files, _ = git("show", "--name-only", "--format=", sha)
        teams = classify([f for f in files.splitlines() if f.strip()])
        for t in teams:
            by_team.setdefault(t, []).append((sha, subj))

    print(f"미push 커밋 {n}개 — {branch} → {up}\n")
    for team in ["스윙팀", "미장팀", "공용/루트"]:
        rows = by_team.get(team)
        if not rows:
            continue
        print(f"[{team}] {len(rows)}개")
        for sha, subj in rows[:8]:
            print(f"   {sha}  {subj[:64]}")
        if len(rows) > 8:
            print(f"   … 외 {len(rows) - 8}개")
        print()

    if args.dry_run:
        print("dry-run — push 안 함.")
        return 0

    if not args.yes:
        ans = input("이대로 push 할까? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("취소.")
            return 0

    print(f"\npush 중 … git push origin {branch}")
    code, out, err = git("push", "origin", branch)
    if code == 0:
        print(f"✓ 완료 — {n}개 커밋 백업됨 ({up})")
        return 0
    print(f"✗ push 실패:\n{err or out}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
