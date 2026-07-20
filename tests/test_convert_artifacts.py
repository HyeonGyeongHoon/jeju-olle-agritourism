import os

import pytest

from src.ingestion.convert_artifacts import (
    ensure_extracted_dir,
    export_to_csv,
    export_to_json,
    export_to_markdown,
)


@pytest.fixture
def sample_courses():
    return [
        {
            "course_name": "Course 01",
            "total_distance_km": 15.0,
            "estimated_time_hours": 5.0,
            "start_point": "시흥초등학교",
            "end_point": "광치기해변",
            "content": "Course 01 본문 내용입니다.",
            "chunks": ["Chunk 1 텍스트", "Chunk 2 텍스트"],
            "sub_segments": [
                {
                    "sub_segment_name": "1-A구간",
                    "start_point": "시흥초등학교",
                    "end_point": "말미오름",
                    "distance_km": 4.0,
                    "estimated_time_hours": 1.5,
                }
            ],
        }
    ]


def test_ensure_extracted_dir(tmp_path):
    target_dir = os.path.join(tmp_path, "extracted_test")
    res = ensure_extracted_dir(target_dir)
    assert os.path.exists(res)


def test_export_to_markdown(tmp_path, sample_courses):
    md_file = os.path.join(tmp_path, "test_full.md")
    res = export_to_markdown(sample_courses, md_file)
    assert os.path.exists(res)
    with open(res, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Course 01" in content
        assert "15.0 km" in content


def test_export_to_json(tmp_path, sample_courses):
    json_file = os.path.join(tmp_path, "test_meta.json")
    res = export_to_json(sample_courses, json_file)
    assert os.path.exists(res)
    with open(res, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Course 01" in content
        assert "course_description" in content
        assert "stamp_locations" in content
        assert "lunch_info" in content


def test_export_to_csv(tmp_path, sample_courses):
    chunks_csv = os.path.join(tmp_path, "test_chunks.csv")
    wheelchair_csv = os.path.join(tmp_path, "test_wheelchair.csv")
    res_chunks, res_wheelchair = export_to_csv(
        sample_courses, chunks_csv, wheelchair_csv
    )
    assert os.path.exists(res_chunks)
    assert os.path.exists(res_wheelchair)
