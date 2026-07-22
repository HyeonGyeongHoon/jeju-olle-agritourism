from unittest.mock import MagicMock

import pytest

from src.agent import llm_client


def test_chat_completion_missing_api_key(monkeypatch):
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")


def test_chat_completion_success(monkeypatch):
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "추천 답변입니다."}}]
    }
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr(llm_client.requests, "post", mock_post)

    result = llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")

    assert result == "추천 답변입니다."
    called_kwargs = mock_post.call_args.kwargs
    assert called_kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert called_kwargs["json"]["model"] == llm_client.DEFAULT_SOLAR_CHAT_MODEL


def test_chat_completion_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429

    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {
        "choices": [{"message": {"content": "재시도 후 성공 답변"}}]
    }

    mock_post = MagicMock(side_effect=[mock_response_429, mock_response_200])
    monkeypatch.setattr(llm_client.requests, "post", mock_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda secs: None)

    result = llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")

    assert result == "재시도 후 성공 답변"
    assert mock_post.call_count == 2
