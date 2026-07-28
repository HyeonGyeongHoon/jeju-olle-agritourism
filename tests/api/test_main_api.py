import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.main import (
    _check_rate_limit,
    _client_identity,
    _rate_limit_log,
    _stream_until_cancelled,
    _verify_api_key,
    report_event_generator,
)


# --- _verify_api_key ---

def test_verify_api_key_passes_when_no_key_configured(monkeypatch):
    monkeypatch.delenv("REPORT_API_KEY", raising=False)
    _verify_api_key(None)  # 예외 없이 통과해야 함
    _verify_api_key("아무값")


def test_verify_api_key_passes_with_matching_header(monkeypatch):
    monkeypatch.setenv("REPORT_API_KEY", "secret123")
    _verify_api_key("secret123")


def test_verify_api_key_rejects_missing_header(monkeypatch):
    monkeypatch.setenv("REPORT_API_KEY", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        _verify_api_key(None)
    assert exc_info.value.status_code == 401


def test_verify_api_key_rejects_wrong_header(monkeypatch):
    monkeypatch.setenv("REPORT_API_KEY", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        _verify_api_key("wrong-key")
    assert exc_info.value.status_code == 401


# --- _check_rate_limit ---

def test_check_rate_limit_allows_up_to_the_limit_then_blocks():
    _rate_limit_log.pop("test-client-1", None)
    for _ in range(5):
        _check_rate_limit("test-client-1")

    with pytest.raises(HTTPException) as exc_info:
        _check_rate_limit("test-client-1")
    assert exc_info.value.status_code == 429


def test_check_rate_limit_resets_after_window_passes():
    _rate_limit_log.pop("test-client-2", None)
    with patch("src.main.time.monotonic", return_value=1000.0):
        for _ in range(5):
            _check_rate_limit("test-client-2")

    with patch("src.main.time.monotonic", return_value=1000.0):
        with pytest.raises(HTTPException):
            _check_rate_limit("test-client-2")

    # 윈도우(60초)가 지나면 다시 허용되어야 함
    with patch("src.main.time.monotonic", return_value=1061.0):
        _check_rate_limit("test-client-2")  # 예외 없이 통과


def test_check_rate_limit_tracks_clients_independently():
    _rate_limit_log.pop("client-a", None)
    _rate_limit_log.pop("client-b", None)
    for _ in range(5):
        _check_rate_limit("client-a")

    _check_rate_limit("client-b")  # client-a 가 한도를 채웠어도 client-b 는 영향 없어야 함


# --- _client_identity ---

def test_client_identity_prefers_api_key_over_ip():
    request = MagicMock()
    request.client.host = "1.2.3.4"
    assert _client_identity(request, "my-key") == "key:my-key"


def test_client_identity_falls_back_to_ip_without_key():
    request = MagicMock()
    request.client.host = "1.2.3.4"
    assert _client_identity(request, None) == "ip:1.2.3.4"


def test_client_identity_handles_missing_client_info():
    request = MagicMock()
    request.client = None
    assert _client_identity(request, None) == "ip:unknown"


# --- _stream_until_cancelled ---

def test_stream_until_cancelled_stops_once_cancel_event_is_set():
    cancel_event = threading.Event()

    def gen():
        yield "a"
        cancel_event.set()  # "a" 를 처리한 뒤 취소 신호가 켜졌다고 가정
        yield "b"
        yield "c"

    result = list(_stream_until_cancelled(gen(), cancel_event))
    assert result == ["a"]


def test_stream_until_cancelled_yields_everything_when_never_cancelled():
    cancel_event = threading.Event()

    def gen():
        yield "a"
        yield "b"

    result = list(_stream_until_cancelled(gen(), cancel_event))
    assert result == ["a", "b"]


# --- report_event_generator cancellation wiring ---

def test_report_event_generator_sends_generic_error_message_to_client():
    """회귀 방지: 그래프 실행 중 발생한 내부 예외의 상세 문구(DB/API 에러 등 민감할 수 있는
    내용)가 그대로 클라이언트에게 노출되면 안 되고, 일반화된 메시지만 SSE event: error 로
    전달되어야 합니다(인증 없이도 호출 가능한 엔드포인트라 정보 노출 위험이 있었음)."""
    sensitive_detail = "relation \"secret_internal_table\" does not exist at postgres://internal-host"

    def fake_stream(inputs):
        raise RuntimeError(sensitive_detail)
        yield {}  # pragma: no cover - 제너레이터로 만들기 위한 도달 불가 코드

    async def scenario():
        events = []
        with patch("src.main.agent_runtime") as mock_runtime:
            mock_runtime.stream.side_effect = fake_stream
            async for chunk in report_event_generator("테스트 질의"):
                events.append(chunk)
        return events

    events = asyncio.run(scenario())

    error_events = [e for e in events if e.startswith("event: error")]
    assert len(error_events) == 1
    assert sensitive_detail not in error_events[0]
    assert "오류가 발생했습니다" in error_events[0]


def test_report_event_generator_sets_cancel_event_on_early_close():
    """클라이언트가 응답을 끝까지 소비하지 않고 제너레이터를 일찍 닫아도(연결 끊김 상황을 흉내),
    cancel_event 가 세팅되어 백그라운드 스레드에 중단 신호가 전달되어야 한다."""
    cancel_event = threading.Event()

    def fake_stream(inputs):
        for i in range(5):
            time.sleep(0.05)
            yield {"intent_classifier": {"intent_category": "info_lookup"}}

    async def scenario():
        with patch("src.main.agent_runtime") as mock_runtime:
            mock_runtime.stream.side_effect = fake_stream
            gen = report_event_generator("테스트 질의", cancel_event=cancel_event)
            await gen.__anext__()
            await gen.aclose()

    asyncio.run(scenario())

    assert cancel_event.is_set()
