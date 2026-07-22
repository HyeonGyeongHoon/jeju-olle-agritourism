import os

from dotenv import load_dotenv
from src.agent.weather_client import get_seasonal_climate_note, simulate_weather_by_query
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
