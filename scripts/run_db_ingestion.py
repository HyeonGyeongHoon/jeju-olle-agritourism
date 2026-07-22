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


COURSE_CROPS_MAP = {
    "1코스": "감자,당근,무",
    "1-1코스": "땅콩,보리,유채,호밀",
    "2코스": "유채",
    "3-A코스": "귤,감귤",
    "3-B코스": "귤,감귤,녹차",
    "4코스": "귤,감귤",
    "5코스": "귤,감귤,유채",
    "6코스": "보리",
    "7코스": "귤,감귤",
    "7-1코스": "귤,감귤",
    "8코스": "귤,감귤,무,유채",
    "9코스": "귤,감귤,유채",
    "10코스": "보리",
    "10-1코스": "보리",
    "11코스": "귤,감귤,마늘",
    "12코스": "귤,감귤,브로콜리,양배추,콜라비",
    "13코스": "보리,유채",
    "14코스": "귤,감귤,양배추",
    "14-1코스": "녹차",
    "15-A코스": "마늘",
    "15-B코스": "마늘",
    "16코스": "귤,감귤,수박,유채",
    "17코스": "보리",
    "18코스": "배추,수박,유채,참외",
    "19코스": "쪽파,양파",
    "20코스": "당근,마늘,양파",
    "21코스": "감자,당근"
}

COURSE_AREAS_MAP = {
    "1코스": "시흥리,종달리",
    "1-1코스": "연평리",
    "2코스": "온평리,신산리",
    "3-A코스": "난산리,삼달리,신풍리,신천리",
    "3-B코스": "신산리,신풍리,신천리",
    "4코스": "표선리,세화리,토산리",
    "5코스": "남원리,위미리",
    "6코스": "하효동,보목동,서귀동,토평동",
    "7코스": "서홍동,법환동,강정동",
    "7-1코스": "서호동,호근동",
    "8코스": "대포동,색달동,하예동",
    "9코스": "대평리,화순리",
    "10코스": "사계리,덕수리",
    "10-1코스": "가파리",
    "11코스": "신평리,무릉리",
    "12코스": "무릉리,고산리",
    "13코스": "용수리,낙천리,저지리",
    "14코스": "저지리,월령리,옹포리,협재리,금능리",
    "14-1코스": "저지리,서광리",
    "15-A코스": "한림리,대림리,한동리",
    "15-B코스": "한림리,수원리,귀덕리",
    "16코스": "고내리,신엄리,구엄리,하귀리",
    "17코스": "외도동,이호동,도두동,용담동",
    "18코스": "건입동,화북동,삼양동,신촌리,조천리",
    "19코스": "조천리,북촌리,동복리,김녕리",
    "20코스": "김녕리,행원리,평대리,세화리",
    "21코스": "세화리,상도리,하도리,종달리"
}


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
            "crops": COURSE_CROPS_MAP.get(c_name, ""),
            "administrative_areas": COURSE_AREAS_MAP.get(c_name, ""),
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
