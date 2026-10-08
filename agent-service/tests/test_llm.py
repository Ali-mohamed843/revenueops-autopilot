from pathlib import Path

import anthropic
import pytest

from revenueops.agents.llm import CLAUDE_MAX_TOKENS, OTHER_MODEL_MAX_TOKENS, AnthropicLLM
from revenueops.config import Settings


def llm(model: str) -> AnthropicLLM:
    return AnthropicLLM(anthropic.Anthropic(api_key="test"), model)


def test_claude_gets_thinking_and_effort() -> None:
    params = llm("claude-sonnet-5-5").request_params("sys", [], [])
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "medium"}
    assert params["max_tokens"] == CLAUDE_MAX_TOKENS


def test_other_models_get_only_core_fields() -> None:
    params = llm("poolside/laguna-s-2.1:free").request_params("sys", [{"role": "user", "content": "x"}], [])
    assert set(params) == {"model", "system", "messages", "tools", "max_tokens"}
    assert params["max_tokens"] == OTHER_MODEL_MAX_TOKENS


def test_openrouter_settings_build_a_client_with_its_base_url(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_BASE_URL=https://openrouter.ai/api\nANTHROPIC_AUTH_TOKEN=sk-or-test\nAGENT_MODEL=some/model:free\n"
    )
    client = AnthropicLLM.from_settings(Settings(_env_file=env))  # type: ignore[call-arg]
    assert client.model == "some/model:free"
    assert str(client.client.base_url).startswith("https://openrouter.ai/api")
    assert client.client.auth_token == "sk-or-test"


def test_missing_credentials_are_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("AGENT_MODEL=claude-sonnet-5-5\n")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM.from_settings(Settings(_env_file=env))  # type: ignore[call-arg]


def test_dotenv_wins_over_the_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Claude Code exports ANTHROPIC_BASE_URL; the project's .env must still decide.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.example")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_BASE_URL=https://openrouter.ai/api\n")
    assert Settings(_env_file=env).anthropic_base_url == "https://openrouter.ai/api"  # type: ignore[call-arg]
