"""
scripts/run_db_ingestion.py
============================
정제된 course_detail_texts.json 과 courses_metadata.json, wheelchair_segments.csv 데이터를 통합하여
Supabase Database 및 Solar Vector Embeddings (4096차원)에 재적재합니다.
"""

import csv
import json
import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.database_loader import (
    get_supabase_client,
    load_courses_to_db,
    load_safety_etiquette_to_db,
    load_wheelchair_segments_to_db,
)
def chunk_by_subtitle(text: str) -> list[str]:
    """본문 텍스트를 단락/소제목 단위(\n\n)로 청킹합니다."""
    if not text:
        return []
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return chunks if chunks else [text.strip()]


def run():
    print("[*] DB 재적재 작업 시작...")

    detail_texts_path = "data/extracted/course_detail_texts.json"
    metadata_path = "data/extracted/courses_metadata.json"
    wheelchair_path = "data/extracted/wheelchair_segments.csv"
    safety_guide_path = "data/extracted/safety_etiquette_guide.json"

    if not os.path.exists(detail_texts_path):
        raise FileNotFoundError(f"{detail_texts_path} 파일이 존재하지 않습니다.")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"{metadata_path} 파일이 존재하지 않습니다.")

    # 1. 데이터 로드
    with open(detail_texts_path, "r", encoding="utf-8") as f:
        detail_data = json.load(f)

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata_list = json.load(f)

    safety_guide_data = {}
    if os.path.exists(safety_guide_path):
        with open(safety_guide_path, "r", encoding="utf-8") as f:
            safety_guide_data = json.load(f)
        print(f"[+] 안전 및 에티켓 가이드 데이터 로드 완료")

    detail_map = {item["course_name"]: item.get("detail_text", "") for item in detail_data}

    # 2. 코스 데이터 통합 및 청킹
    courses_to_load = []
    total_chunks = 0

    for meta in metadata_list:
        c_name = meta["course_name"]
        detail_text = detail_map.get(c_name, "")
        chunks = chunk_by_subtitle(detail_text) if detail_text else []
        total_chunks += len(chunks)

        course_obj = {
            "course_name": c_name,
            "opening_date": meta.get("opening_date", ""),
            "total_distance_km": meta.get("total_distance_km", 0.0),
            "estimated_time_hours": meta.get("estimated_time_hours", 0.0),
            "estimated_time_text": meta.get("estimated_time_text", ""),
            "difficulty": meta.get("difficulty", "중"),
            "course_description": meta.get("course_description", ""),
            "has_wheelchair_segment": meta.get("has_wheelchair_segment", "없음"),
            "start_point": meta.get("start_point", "미정"),
            "end_point": meta.get("end_point", "미정"),
            "stamp_locations": meta.get("stamp_locations", ""),
            "lunch_info": meta.get("lunch_info", ""),
            "sub_segments": [
                {
                    "sub_segment_name": sub.get("segment_name") or sub.get("sub_segment_name", "구간"),
                    "start_point": meta.get("start_point", "미정"),
                    "end_point": meta.get("end_point", "미정"),
                    "distance_km": sub.get("distance_from_start_km") or sub.get("distance_km", 0.0),
                    "estimated_time_hours": 0.0,
                    "description": "",
                }
                for sub in meta.get("sub_segments", [])
            ],
            "chunks": chunks,
        }
        courses_to_load.append(course_obj)

    print(f"[+] 준비된 코스 수: {len(courses_to_load)} 개, 생성된 총 본문 청크 수: {total_chunks} 개")

    # 3. 휠체어 구간 데이터 로드
    wheelchair_segments = []
    if os.path.exists(wheelchair_path):
        with open(wheelchair_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                wheelchair_segments.append(
                    {
                        "segment_name": row.get("segment_name", ""),
                        "start_address": row.get("start_address", ""),
                        "distance_km": float(row.get("distance_km", 0.0)) if row.get("distance_km") else 0.0,
                        "difficulty_level": row.get("difficulty_level", "중"),
                    }
                )
        print(f"[+] 준비된 휠체어 구간 수: {len(wheelchair_segments)} 개")

    # 4. Supabase DB 접속 및 적재
    client = get_supabase_client()

    print("[*] 안전 수칙 및 에티켓 가이드 적재 중...")
    load_safety_etiquette_to_db(client, safety_guide_data)
    print("[+] 안전 수칙 및 에티켓 가이드 적재 완료!")

    print("[*] 휠체어 보행 구간 적재 중...")
    load_wheelchair_segments_to_db(client, wheelchair_segments)
    print("[+] 휠체어 보행 구간 완료!")

    print("[*] 코스 메타데이터, 세부 구간 및 Solar 임베딩 청크 DB 적재 시작...")
    success = load_courses_to_db(client, courses_to_load)

    if success:
        print("\n[OK] Supabase DB 및 Solar 임베딩 재적재가 성공적으로 진행 완료되었습니다!")
    else:
        print("\n[!] DB 적재 중 일부 오류가 발생했습니다.")


if __name__ == "__main__":
    run()
