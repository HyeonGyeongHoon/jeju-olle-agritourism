from unittest.mock import MagicMock

import pytest

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
