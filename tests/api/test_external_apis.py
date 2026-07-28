import json
import os
from unittest.mock import patch

from dotenv import load_dotenv
from src.agent import weather_client
from src.agent.weather_client import assess_weather_risk_from_query, get_seasonal_climate_note
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations

load_dotenv()


def test_external_api_keys_loaded():
    """비짓제주 API 키(오프라인 DB 재적재 스크립트에서 여전히 사용)가 환경 변수로부터 정상 로드되는지 검증합니다."""
    visit_jeju_key = os.getenv("VISIT_JEJU_API_KEY")

    assert visit_jeju_key is not None, "VISIT_JEJU_API_KEY 환경 변수가 로드되지 않았습니다."
    assert len(visit_jeju_key.strip()) > 0, "VISIT_JEJU_API_KEY 가 빈 값입니다. .env 파일을 확인해 주세요."


def test_get_seasonal_climate_note_spring_is_safe():
    """봄철(3~5월)은 SAFE 상태의 쾌청한 계절 특성을 반환하는지 검증합니다."""
    for month in [3, 4, 5]:
        note = get_seasonal_climate_note(month)
        assert note["status"] == "SAFE"
        assert len(note["warnings"]) == 0


def test_get_seasonal_climate_note_winter_is_warning():
    """한파·강풍이 잦은 겨울철(12~2월)은 WARNING 상태를 반환하는지 검증합니다."""
    for month in [12, 1, 2]:
        note = get_seasonal_climate_note(month)
        assert note["status"] == "WARNING"
        assert note["warnings"]


def test_get_seasonal_climate_note_typhoon_season_has_guideline():
    """태풍 영향이 잦은 9월은 WARNING 상태와 함께 동선 가이드라인을 반환하는지 검증합니다."""
    note = get_seasonal_climate_note(9)
    assert note["status"] == "WARNING"
    assert "태풍" in "".join(note["warnings"]) or "태풍" in note["description"]
    assert note.get("guideline")


def test_get_seasonal_climate_note_unknown_month_falls_back_safe():
    """정의되지 않은 월 입력 시 예외 없이 SAFE 기본값으로 폴백하는지 검증합니다."""
    note = get_seasonal_climate_note(0)
    assert note["status"] == "SAFE"
    assert len(note["warnings"]) == 0


def _mock_llm_status(status: str, reason: str = "테스트 판단"):
    return json.dumps({"status": status, "reason": reason})


def test_weather_risk_assessment_danger_when_query_asks_about_current_risk():
    """지금/이번 방문 시점의 실제 위험을 묻는 질문은 DANGER로 판단해야 합니다."""
    with patch.object(weather_client, "get_chat_completion", return_value=_mock_llm_status("DANGER")):
        weather = assess_weather_risk_from_query("태풍 오는데 지금 걸어도 될까요?")

    assert weather["status"] == "DANGER"
    assert weather["warnings"]


def test_weather_risk_assessment_safe_for_past_tense_mention():
    """회귀 방지: "작년 태풍 피해가 컸다던데" 처럼 날씨 위험 단어가 있어도 과거를 언급하는
    질문은, 예전 키워드 매칭 방식과 달리 DANGER로 오판하면 안 됩니다(2026-07-24 QA 리뷰 지적)."""
    with patch.object(weather_client, "get_chat_completion", return_value=_mock_llm_status("SAFE")):
        weather = assess_weather_risk_from_query("작년 태풍 피해가 컸다던데, 그때도 이 코스 걸을 수 있었나요?")

    assert weather["status"] == "SAFE"
    assert weather["warnings"] == []


def test_weather_risk_assessment_safe_for_clear_weather_query():
    with patch.object(weather_client, "get_chat_completion", return_value=_mock_llm_status("SAFE")):
        weather = assess_weather_risk_from_query("날씨가 맑은데 추천해줘")

    assert weather["status"] == "SAFE"
    assert len(weather["warnings"]) == 0


def test_weather_risk_assessment_falls_back_to_safe_on_llm_failure():
    """LLM 호출 자체가 실패해도(네트워크 오류, malformed JSON 등) DANGER로 폴백하면 안 됩니다 —
    이 함수의 목적 자체가 오탐지로 인한 불필요한 안전 우회를 없애는 것이므로, 장애 시 DANGER로
    폴백하면 같은 문제가 형태만 바뀌어 재발합니다."""
    with patch.object(weather_client, "get_chat_completion", side_effect=Exception("네트워크 오류")):
        weather = assess_weather_risk_from_query("태풍 오는데 갈 수 있나요?")

    assert weather["status"] == "SAFE"


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
