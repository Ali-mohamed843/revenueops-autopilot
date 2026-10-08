import pytest

from revenueops import cli
from revenueops.adapters.base import StoreError


def test_configuration_errors_are_one_line_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing_key() -> int:
        raise ValueError("STOREFORGE_API_KEY is not set")

    monkeypatch.setattr(cli, "cmd_detect", missing_key)
    assert cli.main(["detect"]) == 2
    assert capsys.readouterr().err == "error: STOREFORGE_API_KEY is not set\n"


def test_store_errors_suggest_what_to_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def down(limit: int, case_id: str | None) -> int:
        raise StoreError("StoreForge is unreachable: connection refused")

    monkeypatch.setattr(cli, "cmd_investigate", down)
    assert cli.main(["investigate"]) == 2
    assert "INTEGRATION_API_KEY" in capsys.readouterr().err
