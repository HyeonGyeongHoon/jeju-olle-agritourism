import pytest

from src.models.schema import WheelchairSegmentSchema


def test_static_wheelchair_data_integrity():
    """정적으로 시딩(Seed)되는 휠체어 구간 데이터 10개의 무결성이 Pydantic 검증 모델을 충족하는지 검증합니다."""
    # 1. 시딩용 하드코딩된 10개 데이터 명세 정의
    static_segments = [
        {
            "segment_name": "1코스 휠체어 구간 (종달리 옛 소금밭 ~ 성산갑문 입구 구간)",
            "start_address": "제주시 구좌읍 종달리 814-5",
            "distance_km": 4.6,
            "difficulty_level": "중",
        },
        {
            "segment_name": "10-1코스 휠체어 구간 (가파도 전 구간)",
            "start_address": "가파도 상동포구",
            "distance_km": 4.2,
            "difficulty_level": "상",
        },
        {
            "segment_name": "4코스 휠체어 구간 (해비치호텔&리조트 ~ 가마리개 쉼터 구간)",
            "start_address": "서귀포시 표선면 표선리 40-76",
            "distance_km": 4.8,
            "difficulty_level": "중",
        },
        {
            "segment_name": "5코스 휠체어 구간 (국립수산과학원 ~ 위미항 구간)",
            "start_address": "서귀포시 남원읍 위미리 785-1",
            "distance_km": 2.7,
            "difficulty_level": "상",
        },
        {
            "segment_name": "6코스 휠체어 구간 (쇠소깍 ~ 보목포구 구간)",
            "start_address": "서귀포시 하효동 999",
            "distance_km": 2.6,
            "difficulty_level": "중",
        },
        {
            "segment_name": "8코스 휠체어 구간 (논짓물 ~ 대평포구)",
            "start_address": "서귀포시 하예동 532-3",
            "distance_km": 3.6,
            "difficulty_level": "상",
        },
        {
            "segment_name": "10코스 휠체어 구간 (사계포구 ~ 송악산 주차장 구간)",
            "start_address": "서귀포시 안덕면 사계리 2125",
            "distance_km": 2.9,
            "difficulty_level": "중",
        },
        {
            "segment_name": "12코스 휠체어 구간 (엉알길 입구 ~ 자구내포구 입구 구간)",
            "start_address": "제주시 한경면 고산리 3674-2",
            "distance_km": 1.1,
            "difficulty_level": "중",
        },
        {
            "segment_name": "14코스 휠체어 구간 (일성콘도 ~ 금능해수욕장 입구 구간)",
            "start_address": "제주시 한림읍 금능리 1621-6",
            "distance_km": 2.1,
            "difficulty_level": "중",
        },
        {
            "segment_name": "17코스 휠체어 구간 (도두봉 내려오는 길 ~ 용연다리 구간)",
            "start_address": "제주시 도두2동 1611",
            "distance_km": 4.4,
            "difficulty_level": "중",
        },
    ]

    # 2. 모든 데이터에 대해 Pydantic schema validation 통과 여부 검증
    for segment in static_segments:
        # ValidationError가 발생하지 않고 정상 파싱이 가능한지 단언
        try:
            validated = WheelchairSegmentSchema(**segment)
            assert validated.distance_km == segment["distance_km"]
            assert validated.difficulty_level == segment["difficulty_level"]
        except Exception as e:
            pytest.fail(
                f"Static wheelchair segment validation failed for: {segment}. Error: {e}"
            )

    assert len(static_segments) == 10
