from unittest.mock import MagicMock

import pytest

from src.ingestion import database_loader
from src.ingestion.database_loader import (
    get_solar_embedding,
    load_courses_to_db,
    load_wheelchair_segments_to_db,
)


def test_database_loaders_with_mock():
    mock_client = MagicMock()
    assert load_courses_to_db(mock_client, []) is True
    assert load_wheelchair_segments_to_db(mock_client, []) is True


def test_solar_embedding_missing_key(monkeypatch):
    # API 키 미설정 시 ValueError 발생 검증
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        get_solar_embedding("테스트 텍스트")


def test_solar_embedding_retries_on_500_then_succeeds(monkeypatch):
    """5xx(서버 쪽 일시적 문제)는 429 와 마찬가지로 재시도 대상이어야 합니다."""
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response_500 = MagicMock()
    mock_response_500.status_code = 503

    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}

    mock_post = MagicMock(side_effect=[mock_response_500, mock_response_200])
    monkeypatch.setattr(database_loader.requests, "post", mock_post)
    monkeypatch.setattr(database_loader.time, "sleep", lambda secs: None)

    result = get_solar_embedding("테스트 텍스트")

    assert result == [0.1, 0.2]
    assert mock_post.call_count == 2


def test_solar_embedding_does_not_retry_on_non_429_4xx(monkeypatch):
    """회귀 방지: 429가 아닌 4xx(예: 401 인증 실패)는 재시도해도 절대 성공하지 않으므로
    즉시 실패해야 합니다 — 예전에는 이런 경우도 최대 5회(최대 약 30초) 재시도했습니다."""
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")

    mock_response_401 = MagicMock()
    mock_response_401.status_code = 401
    mock_response_401.raise_for_status.side_effect = Exception("401 Client Error")

    mock_post = MagicMock(return_value=mock_response_401)
    monkeypatch.setattr(database_loader.requests, "post", mock_post)

    sleep_calls = []
    monkeypatch.setattr(database_loader.time, "sleep", lambda secs: sleep_calls.append(secs))

    with pytest.raises(Exception, match="401 Client Error"):
        get_solar_embedding("테스트 텍스트")

    assert mock_post.call_count == 1
    assert sleep_calls == []
