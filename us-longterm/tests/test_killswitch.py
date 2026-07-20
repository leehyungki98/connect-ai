"""킬스위치 — engage/reset/status + 상태 파일 손상 시 fail-closed."""
from longcore.safety import killswitch


def _tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(killswitch, "STATE_FILE", tmp_path / "killswitch.json")


def test_initial_status_disengaged(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    st = killswitch.status()
    assert st["engaged"] is False and st["corrupt"] is False


def test_engage_and_reset(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    st = killswitch.engage("테스트 사유")
    assert st["engaged"] is True and st["reason"] == "테스트 사유"
    st = killswitch.reset()
    assert st["engaged"] is False


def test_corrupt_state_fails_closed(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    killswitch.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    killswitch.STATE_FILE.write_text("{{{ 깨진 JSON", encoding="utf-8")
    st = killswitch.status()
    assert st["engaged"] is True and st["corrupt"] is True   # 차단으로 간주


def test_wrong_type_fails_closed(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    killswitch.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    killswitch.STATE_FILE.write_text('{"engaged": "no"}', encoding="utf-8")
    assert killswitch.status()["engaged"] is True
