from autotrader.safety.killswitch import KillSwitch


def test_default_disengaged(tmp_path):
    ks = KillSwitch(tmp_path / "ks.json")
    assert ks.is_engaged() is False


def test_engage_blocks_and_persists_across_restart(tmp_path):
    f = tmp_path / "ks.json"
    KillSwitch(f).engage("수동 테스트")
    # 프로세스 재시작 시뮬레이션: 새 인스턴스로 같은 파일 읽기
    ks2 = KillSwitch(f)
    assert ks2.is_engaged() is True
    assert ks2.state().reason == "수동 테스트"


def test_reset_is_the_only_way_out(tmp_path):
    f = tmp_path / "ks.json"
    ks = KillSwitch(f)
    ks.engage("x")
    assert ks.is_engaged() is True
    ks.reset()
    assert ks.is_engaged() is False


def test_corrupted_state_file_fails_closed(tmp_path):
    f = tmp_path / "ks.json"
    f.write_text("{invalid json!!", encoding="utf-8")
    ks = KillSwitch(f)
    assert ks.is_engaged() is True
    assert "fail-closed" in ks.state().reason


def test_missing_keys_fail_closed(tmp_path):
    f = tmp_path / "ks.json"
    f.write_text('{"foo": 1}', encoding="utf-8")
    assert KillSwitch(f).is_engaged() is True
