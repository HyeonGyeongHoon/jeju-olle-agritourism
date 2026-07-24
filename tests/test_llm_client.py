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


def test_chat_completion_retries_on_500_then_succeeds(monkeypatch):
    """5xx(서버 쪽 일시적 문제)는 429 와 마찬가지로 재시도 대상이어야 합니다."""
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response_500 = MagicMock()
    mock_response_500.status_code = 503

    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {
        "choices": [{"message": {"content": "재시도 후 성공 답변"}}]
    }

    mock_post = MagicMock(side_effect=[mock_response_500, mock_response_200])
    monkeypatch.setattr(llm_client.requests, "post", mock_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda secs: None)

    result = llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")

    assert result == "재시도 후 성공 답변"
    assert mock_post.call_count == 2


def test_chat_completion_does_not_retry_on_non_429_4xx(monkeypatch):
    """회귀 방지: 429가 아닌 4xx(예: 401 인증 실패, 400 잘못된 요청)는 재시도해도 절대
    성공하지 않으므로 즉시 실패해야 합니다 — 예전에는 이런 경우도 최대 5회(최대 약 30초)
    재시도하며 시간을 낭비했었습니다."""
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response_401 = MagicMock()
    mock_response_401.status_code = 401
    mock_response_401.raise_for_status.side_effect = Exception("401 Client Error")

    mock_post = MagicMock(return_value=mock_response_401)
    monkeypatch.setattr(llm_client.requests, "post", mock_post)

    sleep_calls = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda secs: sleep_calls.append(secs))

    with pytest.raises(Exception, match="401 Client Error"):
        llm_client.get_chat_completion("시스템 프롬프트", "사용자 메시지")

    assert mock_post.call_count == 1  # 재시도 없이 딱 1번만 호출됨
    assert sleep_calls == []  # 재시도 대기(sleep)도 전혀 없어야 함
