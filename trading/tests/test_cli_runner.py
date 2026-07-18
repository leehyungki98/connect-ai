"""_run_cli 회귀 테스트: PATH 미존재 CLI는 명확한 에러로 실패."""
import pytest

from autotrader.brain import client


def test_missing_cli_raises_clear_error(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        client._run_cli("codex", "test")
