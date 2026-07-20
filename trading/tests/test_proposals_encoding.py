"""승인 게이트가 한국어·em dash diff 를 처리하는지.

2026-07-20 실사고: git apply 에 diff 를 text=True 로 넘기는데 Windows 기본
인코딩(cp949)이 em dash(U+2014)를 못 써서 stdin 쓰기 스레드가 죽고, git 이
입력을 기다리다 60초 타임아웃. 승인 게이트 전체가 멎었다. 이 코드베이스의
diff 에는 한국어 주석과 em dash 가 일상적으로 들어간다.
"""
import subprocess

from autotrader.proposals import ProposalQueue

DIFF_WITH_EM_DASH = """diff --git a/note.md b/note.md
--- a/note.md
+++ b/note.md
@@ -1 +1,2 @@
 hello
+한국어 주석 — em dash 포함
"""


def _init_repo(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "note.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_approve_handles_non_cp949_diff(tmp_path):
    """em dash 가 든 diff 도 타임아웃 없이 적용된다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    q = ProposalQueue(
        base_dir=tmp_path / "q", repo_root=repo,
        test_runner=lambda _: (True, "ok"),  # 테스트 러너는 이 케이스의 관심사가 아님
    )
    card = q.submit("분석", "진단", "제안", DIFF_WITH_EM_DASH).card
    r = q.approve(card.id)

    assert r.card.status == "approved", f"승인 실패: {r.message}"
    assert "—" in (repo / "note.md").read_text(encoding="utf-8")


def test_failed_tests_roll_back_the_diff(tmp_path):
    """테스트가 깨지면 적용분을 되돌린다 — 나쁜 카드가 코드를 남기면 안 된다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    q = ProposalQueue(
        base_dir=tmp_path / "q", repo_root=repo,
        test_runner=lambda _: (False, "1 failed"),
    )
    card = q.submit("분석", "진단", "제안", DIFF_WITH_EM_DASH).card
    r = q.approve(card.id)

    assert r.card.status == "needs_revision"
    assert (repo / "note.md").read_text(encoding="utf-8") == "hello\n", "롤백 안 됨"
