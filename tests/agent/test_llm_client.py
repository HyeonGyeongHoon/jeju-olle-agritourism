from unittest.mock import MagicMock, patch
import pytest
from src.agent import llm_client


def test_chat_completion_missing_api_key(monkeypatch):
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")


def test_chat_completion_success(monkeypatch):
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.return_value = MagicMock(content="추천 답변입니다.")

    with patch("src.agent.llm_client.ChatOpenAI", return_value=mock_llm_instance) as mock_chat_openai:
        result = llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")

        assert result == "추천 답변입니다."
        mock_chat_openai.assert_called_once_with(
            openai_api_key="test-key",
            openai_api_base="https://api.upstage.ai/v1",
            model_name=llm_client.DEFAULT_SOLAR_CHAT_MODEL,
            max_retries=5,
            timeout=30,
            temperature=0,
        )


def test_chat_completion_failure_propagation(monkeypatch):
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke.side_effect = RuntimeError("API Error")

    with patch("src.agent.llm_client.ChatOpenAI", return_value=mock_llm_instance):
        with pytest.raises(RuntimeError, match="API Error"):
            llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")
