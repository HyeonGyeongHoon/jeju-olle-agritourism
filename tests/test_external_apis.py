import os
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from src.agent import weather_client
from src.agent.weather_client import get_current_weather, simulate_weather_by_query
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations, _get_mock_recommendations

load_dotenv()


def _mock_kma_response(result_code: str, items=None):
    body = {"items": {"item": items}} if items is not None else {}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "OK"},
            "body": body
        }
    }
    return mock_response


def test_external_api_keys_loaded():
    """선택적 외부 연동 API Key(기상청, 비짓제주)가 환경 변수로부터 성공적으로 로드되었는지 검증합니다."""
    visit_jeju_key = os.getenv("VISIT_JEJU_API_KEY")
    kma_key = os.getenv("KMA_API_KEY")
    
    assert visit_jeju_key is not None, "VISIT_JEJU_API_KEY 환경 변수가 로드되지 않았습니다."
    assert kma_key is not None, "KMA_API_KEY 환경 변수가 로드되지 않았습니다."
    assert len(visit_jeju_key.strip()) > 0, "VISIT_JEJU_API_KEY 가 빈 값입니다. .env 파일을 확인해 주세요."
    assert len(kma_key.strip()) > 0, "KMA_API_KEY 가 빈 값입니다. .env 파일을 확인해 주세요."


def test_weather_client_safe_by_default():
    """기상청 API 키 미설정 시 기본적으로 SAFE 상태의 모의 데이터를 정상적으로 반환하는지 검증합니다."""
    # API 키가 환경변수에 없을 때 검증
    # 만약 있다면 제거하고 테스트 (임시 격리)
    original_key = os.environ.get("KMA_API_KEY")
    try:
        if "KMA_API_KEY" in os.environ:
            del os.environ["KMA_API_KEY"]
            
        weather = get_current_weather("성산읍")
        assert weather["status"] == "SAFE"
        assert weather["temperature"] == 22.5
        assert "warnings" in weather
        assert len(weather["warnings"]) == 0
    finally:
        if original_key is not None:
            os.environ["KMA_API_KEY"] = original_key


def test_get_current_weather_active_warning_triggers_warning(monkeypatch):
    """활성(미해제) 강풍주의보 특보가 있으면 WARNING 상태로 판정하는지 검증합니다."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    mock_response = _mock_kma_response("00", items=[
        {"stnId": "184", "title": "[특보] 제07-1호 : 2026.07.22.10:00 / 강풍주의보 발표 (*)", "tmFc": 202607221000}
    ])
    monkeypatch.setattr(weather_client.requests, "get", MagicMock(return_value=mock_response))

    weather = get_current_weather("제주")

    assert weather["status"] == "WARNING"
    assert any("강풍주의보" in w for w in weather["warnings"])


def test_get_current_weather_danger_keyword_triggers_danger(monkeypatch):
    """태풍/경보 등급 특보가 있으면 DANGER 상태로 판정하는지 검증합니다."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    mock_response = _mock_kma_response("00", items=[
        {"stnId": "189", "title": "[특보] 제07-2호 : 2026.07.22.10:00 / 태풍경보 발표 (*)", "tmFc": 202607221000}
    ])
    monkeypatch.setattr(weather_client.requests, "get", MagicMock(return_value=mock_response))

    weather = get_current_weather("성산읍")

    assert weather["status"] == "DANGER"


def test_get_current_weather_ignores_lifted_warning(monkeypatch):
    """이미 해제(해제)된 특보는 활성 위험으로 카운트하지 않고 SAFE로 판정하는지 검증합니다."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    mock_response = _mock_kma_response("00", items=[
        {"stnId": "184", "title": "[특보] 제07-3호 : 2026.07.22.10:00 / 호우주의보 해제 (*)", "tmFc": 202607221000}
    ])
    monkeypatch.setattr(weather_client.requests, "get", MagicMock(return_value=mock_response))

    weather = get_current_weather("제주")

    assert weather["status"] == "SAFE"
    assert len(weather["warnings"]) == 0


def test_get_current_weather_no_data_result_code_is_safe(monkeypatch):
    """resultCode=03(NO_DATA) 응답을 정상적인 무특보 상태(SAFE)로 처리하는지 검증합니다."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    mock_response = _mock_kma_response("03")
    monkeypatch.setattr(weather_client.requests, "get", MagicMock(return_value=mock_response))

    weather = get_current_weather("제주")

    assert weather["status"] == "SAFE"


def test_get_current_weather_http_error_falls_back_safe(monkeypatch):
    """HTTP 비정상 상태 코드 응답 시 예외 없이 SAFE 로 안전하게 폴백하는지 검증합니다."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Unexpected errors"
    monkeypatch.setattr(weather_client.requests, "get", MagicMock(return_value=mock_response))

    weather = get_current_weather("제주")

    assert weather["status"] == "SAFE"


def test_resolve_stn_id_maps_seogwipo_area_tokens():
    """서귀포시 권역 행정구역명은 서귀포 지점코드(189)로, 그 외는 제주시 지점코드(184)로 매핑되는지 검증합니다."""
    assert weather_client._resolve_stn_id("성산읍") == weather_client._SEOGWIPO_SI_STN_ID
    assert weather_client._resolve_stn_id("대정읍") == weather_client._SEOGWIPO_SI_STN_ID
    assert weather_client._resolve_stn_id("구좌읍") == weather_client._JEJU_SI_STN_ID
    assert weather_client._resolve_stn_id("제주") == weather_client._JEJU_SI_STN_ID


def test_weather_client_simulation():
    """질문 키워드에 따른 날씨 위험도(DANGER/WARNING) 시뮬레이션 검증합니다."""
    # 1. 태풍 키워드 테스트 (DANGER)
    danger_weather = simulate_weather_by_query("태풍 오는데 갈 수 있나요?")
    assert danger_weather["status"] == "DANGER"
    assert "태풍경보" in danger_weather["warnings"][0]
    
    # 2. 비/강풍 키워드 테스트 (WARNING)
    warning_weather = simulate_weather_by_query("비바람이 많이 칩니다.")
    assert warning_weather["status"] == "WARNING"
    assert "강풍주의보" in warning_weather["warnings"][0]
    
    # 3. 안전한 맑은 키워드 테스트 (SAFE)
    safe_weather = simulate_weather_by_query("날씨가 맑은데 추천해줘")
    assert safe_weather["status"] == "SAFE"
    assert len(safe_weather["warnings"]) == 0


def test_visit_jeju_client_mock_data():
    """비짓제주 API 가 오프라인이거나 키가 없을 때 작물 및 행정구역 매핑 Mock 매장을 정상 반환하는지 검증합니다."""
    # 키가 없는 상태로 모의 데이터 테스트
    original_key = os.environ.get("VISIT_JEJU_API_KEY")
    try:
        if "VISIT_JEJU_API_KEY" in os.environ:
            del os.environ["VISIT_JEJU_API_KEY"]
            
        # 당근 - 종달리 매칭
        recommendations = get_visit_jeju_recommendations("당근", "종달리")
        assert len(recommendations) > 0
        assert all(rec["crop_tag"] == "당근" for rec in recommendations)
        assert any("종달리" in rec["address"] for rec in recommendations)
        
        # 감귤 - 위미리 매칭
        recs_citrus = get_visit_jeju_recommendations("감귤", "위미리")
        assert len(recs_citrus) > 0
        assert recs_citrus[0]["crop_tag"] == "감귤"
        assert "위미리" in recs_citrus[0]["address"]
    finally:
        if original_key is not None:
            os.environ["VISIT_JEJU_API_KEY"] = original_key


def test_visit_jeju_client_fallback_no_area():
    """요청한 행정구역 내에 해당 작물 매장이 없을 시, 행정구역 제약을 완화하여 전체 작물 매장을 추천하는 Fallback 동작을 검증합니다."""
    original_key = os.environ.get("VISIT_JEJU_API_KEY")
    try:
        if "VISIT_JEJU_API_KEY" in os.environ:
            del os.environ["VISIT_JEJU_API_KEY"]
            
        # 성산읍 신풍리에는 가파도 청보리 매장이 없으나, fallback 에 의해 가파도의 보리 매장이 반환되는지 확인
        recommendations = get_visit_jeju_recommendations("보리", "신풍리")
        assert len(recommendations) > 0
        assert recommendations[0]["crop_tag"] == "보리"
    finally:
        if original_key is not None:
            os.environ["VISIT_JEJU_API_KEY"] = original_key
