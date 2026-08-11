"""목표 3 검증: 승인/거부/수정 흐름 + 게이트 재검증 + 왕복 상한."""
import subprocess

import pytest

from autotrader.proposals import ProposalQueue, forbidden_hit, touched_paths

ZONES = ("trading/autotrader/safety/", "trading/autotrader/config.py")


def make_diff(path, old, new):
    return f"""diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-{old}
+{new}
"""


@pytest.fixture
def repo(tmp_path):
    """실제 git 리포 + 대상 파일 1개."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "strategy.py").write_text("W20 = 0.6\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], check=True,
    )
    return tmp_path


def make_queue(repo, tmp_path, tests_pass=True):
    log = []
    q = ProposalQueue(
        tmp_path / "queue", repo, forbidden=ZONES,
        test_runner=lambda root: (log.append(1), (tests_pass, "163 passed"))[1],
    )
    return q


GOOD_DIFF = make_diff("strategy.py", "W20 = 0.6", "W20 = 0.55")


def submit_ok(q):
    r = q.submit("분석: 최근 눌림목 승률 저하", "진단: 단기 모멘텀 과대 가중",
                 "제안: W20 0.6→0.55", GOOD_DIFF)
    assert r.ok, r.message
    return r.card


# --- 제출 ---

def test_submit_creates_pending_card(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    assert card.status == "pending"
    assert q.list_pending()[0].id == card.id


def test_missing_fields_rejected(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    r = q.submit("", "진단", "제안", GOOD_DIFF)
    assert r.ok is False and "누락" in r.message


def test_forbidden_zone_rejected_at_submit(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    bad = make_diff("trading/autotrader/safety/killswitch.py", "a", "b")
    r = q.submit("a", "b", "c", bad)
    assert r.ok is False and "편집 금지 구역" in r.message
    assert q.list_pending() == []


def test_forbidden_config_file_rejected(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    bad = make_diff("trading/autotrader/config.py", "DAILY_LOSS_LIMIT_BP = 300", "DAILY_LOSS_LIMIT_BP = 9999")
    assert q.submit("a", "b", "c", bad).ok is False


# --- 승인: 적용 + 테스트 재검증 ---

def test_approve_applies_diff_when_tests_pass(repo, tmp_path):
    q = make_queue(repo, tmp_path, tests_pass=True)
    card = submit_ok(q)
    r = q.approve(card.id)
    assert r.ok is True and "테스트 통과" in r.message
    assert (repo / "strategy.py").read_text() == "W20 = 0.55\n"
    assert q.get(card.id).status == "approved"


def test_approve_rolls_back_when_tests_fail(repo, tmp_path):
    q = make_queue(repo, tmp_path, tests_pass=False)
    card = submit_ok(q)
    r = q.approve(card.id)
    assert r.ok is True and "롤백" in r.message  # needs_revision으로 전환
    assert (repo / "strategy.py").read_text() == "W20 = 0.6\n"  # 원상 복구
    assert q.get(card.id).status == "needs_revision"


def test_approve_unappliable_diff_needs_revision(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    r = q.submit("a", "b", "c", make_diff("strategy.py", "없는 내용", "x"))
    r2 = q.approve(r.card.id)
    assert q.get(r.card.id).status == "needs_revision"
    assert "적용 불가" in r2.message


# --- 수정 지시 → 재제출 → 게이트 재검증 ---

def test_revision_then_resubmit_then_approve(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    r = q.request_revision(card.id, "0.55 대신 0.5로")
    assert r.card.status == "needs_revision"
    r2 = q.resubmit(card.id, make_diff("strategy.py", "W20 = 0.6", "W20 = 0.5"))
    assert r2.card.status == "pending"
    assert q.approve(card.id).ok is True
    assert (repo / "strategy.py").read_text() == "W20 = 0.5\n"


def test_resubmit_reenters_forbidden_gate(repo, tmp_path):
    """수정 지시로 들어온 변경도 금지 구역 검사를 재통과해야 한다."""
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    q.request_revision(card.id, "손실 한도를 늘려봐")
    bad = make_diff("trading/autotrader/safety/daily_loss.py", "a", "b")
    r = q.resubmit(card.id, bad)
    assert r.ok is False and "편집 금지 구역" in r.message
    assert q.get(card.id).status == "rejected"


def test_revision_cap_auto_rejects(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    for i in range(3):
        assert q.request_revision(card.id, f"수정 {i}").card.status == "needs_revision"
        assert q.resubmit(card.id, GOOD_DIFF).ok is True
    r = q.request_revision(card.id, "수정 4")  # 4번째 왕복
    assert r.card.status == "rejected" and "상한" in r.message


# --- 기타 ---

def test_reject_moves_to_history(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    q.reject(card.id, "근거 부족")
    assert q.list_pending() == [] and q.get(card.id).status == "rejected"


def test_approve_non_pending_fails(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    card = submit_ok(q)
    q.reject(card.id, "x")
    assert q.approve(card.id).ok is False


def test_promote_rule_appends_to_claude_md(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    q.promote_rule("진입 근거에 거래대금 추세를 반드시 포함할 것")
    q.promote_rule("두 번째 규칙")
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("## 트레이딩 영구 규칙") == 1
    assert "거래대금 추세" in text and "두 번째 규칙" in text


def test_touched_paths_and_forbidden():
    d = make_diff("a/b.py", "x", "y") + make_diff("trading/autotrader/gates/risk.py", "x", "y")
    assert touched_paths(d) == {"a/b.py", "trading/autotrader/gates/risk.py"}
    assert forbidden_hit(d, ("trading/autotrader/gates/",)) == "trading/autotrader/gates/risk.py"
    assert forbidden_hit(d, ("trading/autotrader/safety/",)) is None
