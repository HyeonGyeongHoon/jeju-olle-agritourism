import pytest

from src.ingestion.parser import (
    chunk_by_subtitle,
    parse_courses,
    parse_courses_structured,
)
from src.ingestion.pdf_extractor import extract_pages_from_pdf, extract_text_from_pdf


def test_parse_courses():
    sample_text = (
        "Welcome to Jeju Olle!\n"
        "Course 01\nDistance: 15.0 km, Time: 5.0 hours. Start at Siheung.\n"
        "Course 18-1\nDistance: 9.3 km, Time: 3.5 hours. Chujado segment."
    )
    courses = parse_courses(sample_text)
    assert len(courses) == 2
    assert courses[0]["course_name"] == "1코스"
    assert courses[0]["total_distance_km"] == 15.0
    assert courses[0]["estimated_time_hours"] == 5.0

    assert courses[1]["course_name"] == "18-1코스"
    assert courses[1]["total_distance_km"] == 11.4  # 공식 메타데이터 교정값 적용 11.4km
    assert courses[1]["estimated_time_hours"] == 4.0


def test_parse_courses_structured():
    sample_pages = [
        {
            "page_num": 31,
            "text": "Jeju Olle Route\n01-1\n우도 올레\n개장 : 2009년 05월 23일\n총 길이 : 11.3km\n소요시간 : 5시간\n스탬프 찍는 곳",
        },
        {"page_num": 32, "text": "우도 올레 코스\n휠체어가능구간\n5.5km 우회로"},
        {"page_num": 33, "text": "01-1 우도 올레 본문 텍스트 내용입니다."},
    ]
    courses = parse_courses_structured(sample_pages)
    assert len(courses) == 1
    c = courses[0]
    assert c["course_name"] == "1-1코스"
    assert c["total_distance_km"] == 11.3
    assert "Jeju Olle Route" in c["summary_info"]
    assert "휠체어가능구간" in c["map_info"]
    assert "본문 텍스트 내용입니다" in c["detail_text"]


def test_chunk_by_subtitle():
    sample_course_text = (
        "Course 01 Intro \u2015 Subtitle 1 Content \u2015 Subtitle 2 Content"
    )
    chunks = chunk_by_subtitle(sample_course_text)
    assert len(chunks) == 3
    assert "Course 01 Intro" in chunks[0]
    assert "Subtitle 1 Content" in chunks[1]
    assert "Subtitle 2 Content" in chunks[2]


def test_extract_text_file_not_found():
    with pytest.raises(FileNotFoundError):
        extract_text_from_pdf("non_existent_file.pdf")
    with pytest.raises(FileNotFoundError):
        extract_pages_from_pdf("non_existent_file.pdf")


def test_empty_parse_courses():
    assert parse_courses("") == []
    assert parse_courses(None) == []
    assert parse_courses_structured([]) == []


def test_empty_chunk_by_subtitle():
    assert chunk_by_subtitle("") == []
    assert chunk_by_subtitle(None) == []
