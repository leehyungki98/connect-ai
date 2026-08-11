from autotrader.safety import __main__ as cli
from autotrader.safety.killswitch import KillSwitch


def test_cli_engage_and_reset(tmp_path, monkeypatch, capsys):
    f = tmp_path / "ks.json"
    monkeypatch.setattr(cli, "KILLSWITCH_FILE", f)
    cli.main(["engage", "테스트 정지"])
    assert KillSwitch(f).is_engaged() is True
    cli.main(["status"])
    assert "engaged=True" in capsys.readouterr().out
    cli.main(["reset"])
    assert KillSwitch(f).is_engaged() is False
