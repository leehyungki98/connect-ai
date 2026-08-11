"""목표 1 검증: 한도 초과를 강제로 트리거해 매매가 실제로 차단되는지."""
from datetime import date

from autotrader.safety.guard import SafetyGuard
from autotrader.safety.killswitch import KillSwitch

D1 = date(2026, 7, 20)
D2 = date(2026, 7, 21)
CAP = 10_000_000


def make_guard(tmp_path):
    return SafetyGuard(KillSwitch(tmp_path / "ks.json"), tmp_path / "block.json")


def test_normal_day_allowed(tmp_path):
    g = make_guard(tmp_path)
    d = g.check(D1, CAP, CAP - 100_000)  # -1%
    assert d.allowed is True


def test_forced_breach_blocks_trading(tmp_path):
    """강제 트리거: 손실 -3.5% → 차단."""
    g = make_guard(tmp_path)
    d = g.check(D1, CAP, CAP - 350_000)
    assert d.allowed is False
    assert "daily loss limit breached" in d.reason


def test_stays_blocked_even_after_recovery(tmp_path):
    """한 번 위반하면 손실이 회복돼도 당일은 계속 차단."""
    g = make_guard(tmp_path)
    assert g.check(D1, CAP, CAP - 350_000).allowed is False
    d = g.check(D1, CAP, CAP - 10_000)  # 거의 회복
    assert d.allowed is False
    assert "already breached" in d.reason


def test_block_persists_across_restart(tmp_path):
    g1 = make_guard(tmp_path)
    assert g1.check(D1, CAP, CAP - 350_000).allowed is False
    g2 = make_guard(tmp_path)  # 재시작 시뮬레이션
    assert g2.check(D1, CAP, CAP).allowed is False


def test_next_day_unblocked(tmp_path):
    g = make_guard(tmp_path)
    assert g.check(D1, CAP, CAP - 350_000).allowed is False
    assert g.check(D2, CAP, CAP - 100_000).allowed is True


def test_killswitch_overrides_everything(tmp_path):
    ks = KillSwitch(tmp_path / "ks.json")
    g = SafetyGuard(ks, tmp_path / "block.json")
    ks.engage("수동 정지")
    d = g.check(D1, CAP, CAP + 1_000_000)  # 수익 중이어도
    assert d.allowed is False
    assert "killswitch engaged" in d.reason


def test_corrupted_block_file_fails_closed(tmp_path):
    g = make_guard(tmp_path)
    (tmp_path / "block.json").write_text("garbage", encoding="utf-8")
    assert g.check(D1, CAP, CAP).allowed is False
