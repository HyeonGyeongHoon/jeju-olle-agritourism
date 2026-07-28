import json
import os
import re
import pytest

EXTRACTED_JSON_PATH = os.path.join("data", "extracted", "course_detail_texts.json")


def test_real_json_file_exists():
    """실제 추출된 JSON 파일 존재 여부를 검증합니다."""
    assert os.path.exists(EXTRACTED_JSON_PATH), f"{EXTRACTED_JSON_PATH} 파일이 존재하지 않습니다."


def test_real_json_course_integrity():
    """실제 course_detail_texts.json 데이터의 전수 무결성을 검증합니다."""
    if not os.path.exists(EXTRACTED_JSON_PATH):
        pytest.skip("실제 추출 JSON 파일이 존재하지 않아 건너뜁니다.")

    with open(EXTRACTED_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. 코스 개수 검증 (제주 올레길 27개 내외 코스 존재 여부)
    assert isinstance(data, list), "JSON 루트 데이터는 리스트 형태여야 합니다."
    assert len(data) >= 25, f"추출된 코스 수가 부족합니다. (현재: {len(data)}개)"

    course_names = []
    course_name_pattern = re.compile(r"^\d+(?:-\d+|-[AB])?코스$")

    # 2. 전수 코스 데이터 필드 무결성 검증
    for idx, item in enumerate(data):
        # 필수 키 존재 검증
        assert "course_name" in item, f"{idx}번째 코스에 'course_name' 키가 누락되었습니다."
        assert "title" in item, f"{idx}번째 코스에 'title' 키가 누락되었습니다."
        assert "detail_text" in item, f"{idx}번째 코스에 'detail_text' 키가 누락되었습니다."

        course_name = item["course_name"]
        title = item["title"]
        detail_text = item["detail_text"]

        # Null 및 빈 문자열 검증
        assert course_name and isinstance(course_name, str), f"{idx}번째 코스의 course_name이 유효하지 않습니다."
        assert title and isinstance(title, str), f"{course_name}의 title이 유효하지 않습니다."
        assert detail_text and isinstance(detail_text, str), f"{course_name}의 detail_text가 유효하지 않습니다."

        # 코스명 명명 규칙 검증 (예: 1코스, 1-1코스, 3-A코스, 18-1코스 등)
        assert course_name_pattern.match(course_name), f"코스명 포맷 불일치: {course_name}"

        # 상세 설명 최소 길이 검증 (기본 100자 이상)
        assert len(detail_text.strip()) >= 100, f"{course_name}의 detail_text 길이가 너무 짧습니다. (길이: {len(detail_text)})"

        course_names.append(course_name)

    # 3. 중복 코스 검증
    assert len(course_names) == len(set(course_names)), "중복된 코스명이 존재합니다."


METADATA_JSON_PATH = os.path.join("data", "extracted", "courses_metadata.json")


def test_real_courses_metadata_integrity():
    """실제 courses_metadata.json 데이터 및 Pydantic CourseSchema 무결성을 검증합니다."""
    from src.models.schema import CourseSchema

    if not os.path.exists(METADATA_JSON_PATH):
        pytest.skip("courses_metadata.json 파일이 존재하지 않습니다.")

    with open(METADATA_JSON_PATH, "r", encoding="utf-8") as f:
        metadata_list = json.load(f)

    assert isinstance(metadata_list, list), "metadata_list는 리스트 형태여야 합니다."
    assert len(metadata_list) >= 25, "코스 메타데이터 수량이 부족합니다."

    for item in metadata_list:
        # Pydantic Schema 자동 통과 및 필드 타입/필수값 검증
        course = CourseSchema(**item)
        assert course.course_name
        assert course.total_distance_km >= 0.0
        assert course.estimated_time_hours >= 0.0
        assert course.start_point
        assert course.end_point


SAFETY_GUIDE_JSON_PATH = os.path.join("data", "extracted", "safety_etiquette_guide.json")


def test_real_safety_etiquette_integrity():
    """실제 safety_etiquette_guide.json 데이터의 무결성을 검증합니다."""
    if not os.path.exists(SAFETY_GUIDE_JSON_PATH):
        pytest.skip("safety_etiquette_guide.json 파일이 존재하지 않습니다.")

    with open(SAFETY_GUIDE_JSON_PATH, "r", encoding="utf-8") as f:
        guide = json.load(f)

    assert isinstance(guide, dict), "가이드 데이터는 dict 구조여야 합니다."
    assert "safety_rules" in guide and len(guide["safety_rules"]) > 0
    assert "etiquette" in guide and len(guide["etiquette"]) > 0
    assert "recommended_equipment" in guide and len(guide["recommended_equipment"]) > 0
    assert "travel_planning_tips" in guide and len(guide["travel_planning_tips"]) > 0

