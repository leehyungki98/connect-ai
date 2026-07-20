"""목표 7 검증(개선 루프): GPT 제안 → 코다리 diff → 큐 제출, 무효 출력 fail-closed."""
import json
import subprocess

import pytest

from autotrader.improve import (
    extract_coder_files,
    make_diff,
    run_improve_cycle,
    validate_improvements,
)
from autotrader.proposals import ProposalQueue

ZONES = ("trading/autotrader/safety/", "trading/autotrader/config.py")

GOOD_IMPROVEMENT = {
    "analysis": "최근 10 트레이드 중 7건이 K1 기각",
    "diagnosis": "선정자 목표가가 보수적이라 손익비 미달",
    "proposal": "선정자 프롬프트의 손익비 하한 안내를 1.5로 상향",
}


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "strategy.py").write_text("RR_HINT = 1.2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], check=True,
    )
    return tmp_path


def make_queue(repo, tmp_path):
    return ProposalQueue(tmp_path / "q", repo, forbidden=ZONES,
                         test_runner=lambda root: (True, "ok"))


# 코다리는 diff 가 아니라 파일 전체를 낸다. diff 는 시스템이 git 으로 만든다.
CODER_RESPONSE = (
    "손익비 하한 안내를 1.5로 올리는 것으로 해석해 구현했다.\n"
    "```file:strategy.py\nRR_HINT = 1.5\n```"
)


def proposer_with(items):
    return lambda brain, prompt: json.dumps({"improvements": items})


# --- GPT 제안 검증 ---

def test_valid_improvements_parsed():
    drafts, errs = validate_improvements(json.dumps({"improvements": [GOOD_IMPROVEMENT]}))
    assert len(drafts) == 1 and errs == ()


def test_empty_improvements_is_normal():
    drafts, errs = validate_improvements(json.dumps({"improvements": []}))
    assert drafts == () and errs == ()


def test_invalid_improvements_rejected():
    assert validate_improvements("잡담")[0] == ()
    assert validate_improvements(json.dumps({"improvements": [{"analysis": "x"}]}))[0] == ()
    too_many = json.dumps({"improvements": [GOOD_IMPROVEMENT] * 4})
    assert validate_improvements(too_many)[0] == ()


# --- 코다리 출력 추출 ---

def test_coder_output_extracted():
    summary, files = extract_coder_files(CODER_RESPONSE)
    assert "해석해 구현" in summary
    assert files == [("strategy.py", "RR_HINT = 1.5\n")]


def test_coder_without_file_fence_fails():
    assert extract_coder_files("파일 없이 설명만")[1] == []
    assert extract_coder_files("```file:x.py\n```")[1] == []


def test_generated_diff_applies(repo):
    """diff 를 LLM 이 아니라 git 이 만들므로 적용이 보장된다."""
    d = make_diff(repo, "strategy.py", "RR_HINT = 1.5\n")
    r = subprocess.run(["git", "apply", "--check"], input=d, cwd=repo,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


def test_multi_hunk_diff_applies(repo):
    """여러 곳을 동시에 고쳐도 오프셋이 어긋나지 않는다.

    2026-07-20 실사고: LLM 이 쓴 14 hunk 짜리 diff 가 문맥은 한 줄도 안 틀렸는데
    누적 오프셋 때문에 적용 실패했다. 내용만 받고 diff 를 git 이 만들면 없어진다.
    """
    (repo / "multi.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "multi"], check=True)
    d = make_diff(repo, "multi.py", "a = 9\nb = 2\nc = 9\nd = 4\ne = 9\n")
    r = subprocess.run(["git", "apply", "--check"], input=d, cwd=repo,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


# --- 전체 사이클 ---

def test_full_cycle_submits_card(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    r = run_improve_cycle(
        "요약", q,
        proposer_runner=proposer_with([GOOD_IMPROVEMENT]),
        coder_runner=lambda brain, prompt: CODER_RESPONSE,
        repo_root=repo,
    )
    assert r["drafts"] == 1 and len(r["submitted"]) == 1
    card = q.list_pending()[0]
    assert "[코다리 구현 요약]" in card.proposal
    assert q.approve(card.id).ok is True  # 승인 경로까지 연결 확인
    assert (repo / "strategy.py").read_text() == "RR_HINT = 1.5\n"


def test_coder_diff_into_safety_zone_rejected(repo, tmp_path):
    """코다리가 안전층을 건드리면 큐가 자동 거부 (스펙: 편집 금지 구역)."""
    bad = CODER_RESPONSE.replace("```file:strategy.py",
                                 "```file:trading/autotrader/safety/killswitch.py")
    q = make_queue(repo, tmp_path)
    r = run_improve_cycle(
        "요약", q,
        proposer_runner=proposer_with([GOOD_IMPROVEMENT]),
        coder_runner=lambda brain, prompt: bad,
        repo_root=repo,
    )
    assert r["submitted"] == [] and len(r["rejected"]) == 1
    assert q.list_pending() == []


def test_coder_failure_counts_failed(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    r = run_improve_cycle(
        "요약", q,
        proposer_runner=proposer_with([GOOD_IMPROVEMENT]),
        coder_runner=lambda brain, prompt: "설명만 하고 diff는 안 냄",
    )
    assert r["failed"] == 1 and r["submitted"] == []


def test_invalid_proposer_no_cycle(repo, tmp_path):
    q = make_queue(repo, tmp_path)
    r = run_improve_cycle(
        "요약", q,
        proposer_runner=lambda b, p: "JSON 아님",
        coder_runner=lambda b, p: (_ for _ in ()).throw(AssertionError("호출 금지")),
    )
    assert r["drafts"] == 0 and r["errors"] != []
