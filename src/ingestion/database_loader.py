import os

import requests
from dotenv import load_dotenv

from supabase import Client, create_client

load_dotenv()

UPSTAGE_EMBEDDING_URL = "https://api.upstage.ai/v1/solar/embeddings"


def get_supabase_client() -> Client:
    """Supabase 클라이언트 객체를 초기화하여 반환합니다."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise ValueError("SUPABASE_URL 및 SUPABASE_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(url, key)


import time


def get_solar_embedding(text: str) -> list[float]:
    """Upstage Solar Embedding API를 호출하여 4096차원 임베딩 벡터를 반환합니다. (HTTP 429 지수 백오프 및 3000자 자동 트렁케이션 탑재)"""
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY 환경 변수가 설정되지 않았습니다.")

    url = "https://api.upstage.ai/v1/solar/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Upstage 4000 토큰 한도 초과 방지를 위한 3000자 트렁케이션 안전 조치
    safe_text = text[:3000] if len(text) > 3000 else text

    payload = {
        "model": "embedding-passage",
        "input": safe_text,
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data["data"][0]["embedding"]
            elif response.status_code == 429:
                sleep_time = (attempt + 1) * 2
                time.sleep(sleep_time)
            else:
                response.raise_for_status()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Upstage API 호출 실패 (재시도 초과): {e}")
            time.sleep((attempt + 1) * 2)

    raise RuntimeError("Upstage API 호출 실패 (최대 재시도 횟수 초과)")


def load_courses_to_db(client: Client, courses: list[dict]) -> bool:
    """코스 메타데이터, 세부 구간 메타데이터 및 청크 임베딩 데이터를 Supabase DB에 적재합니다."""
    for course in courses:
        # 1. 코스 메타데이터 적재 또는 업서트 (풀 스키마 시도)
        full_course_data = {
            "course_name": course["course_name"],
            "opening_date": course.get("opening_date", ""),
            "total_distance_km": course.get("total_distance_km", 0.0),
            "estimated_time_hours": course.get("estimated_time_hours", 0.0),
            "estimated_time_text": course.get("estimated_time_text", ""),
            "difficulty": course.get("difficulty", "중"),
            "course_description": course.get("course_description", ""),
            "has_wheelchair_segment": course.get("has_wheelchair_segment", "없음"),
            "start_point": course.get("start_point", "미정"),
            "end_point": course.get("end_point", "미정"),
            "stamp_locations": course.get("stamp_locations", ""),
            "lunch_info": course.get("lunch_info", ""),
        }
        
        basic_course_data = {
            "course_name": course["course_name"],
            "total_distance_km": course.get("total_distance_km", 0.0),
            "estimated_time_hours": course.get("estimated_time_hours", 0.0),
            "start_point": course.get("start_point", "미정"),
            "end_point": course.get("end_point", "미정"),
        }

        try:
            res = (
                client.table("courses")
                .upsert(full_course_data, on_conflict="course_name")
                .execute()
            )
        except Exception as e:
            # DB 스키마 컬럼 미확장 시 기본 컬럼으로 Fallback 적재
            print(f"[!] '{course['course_name']}' 풀 스키마 적재 실패, 기본 컬럼으로 Fallback 적재: {e}")
            res = (
                client.table("courses")
                .upsert(basic_course_data, on_conflict="course_name")
                .execute()
            )

        course_id = res.data[0]["id"] if res.data else None

        if not course_id:
            continue

        # 2. 세부 구간 분할 메타데이터 적재 (신규 기능)
        for sub_seg in course.get("sub_segments", []):
            sub_seg_data = {
                "course_id": course_id,
                "sub_segment_name": sub_seg["sub_segment_name"],
                "start_point": sub_seg["start_point"],
                "end_point": sub_seg["end_point"],
                "distance_km": sub_seg.get("distance_km", 0.0),
                "estimated_time_hours": sub_seg.get("estimated_time_hours", 0.0),
                "description": sub_seg.get("description", ""),
            }
            try:
                client.table("course_sub_segments").insert(sub_seg_data).execute()
            except Exception as e:
                print(f"[!] '{course['course_name']}' 세부 구간 '{sub_seg_data['sub_segment_name']}' 적재 실패: {e}")

        # 3. 청크 텍스트 및 Solar 임베딩 벡터 적재
        for chunk in course.get("chunks", []):
            embedding_vector = get_solar_embedding(chunk)
            chunk_data = {
                "course_id": course_id,
                "title": f"{course['course_name']} 세부 구간",
                "content": chunk,
                "embedding": embedding_vector,
            }
            client.table("course_chunks").insert(chunk_data).execute()

    return True


def load_wheelchair_segments_to_db(client: Client, segments: list[dict]) -> bool:
    """휠체어 구간 정보를 Supabase RDB에 적재합니다."""
    if segments:
        try:
            client.table("wheelchair_accessible_segments").upsert(segments).execute()
        except Exception as e:
            print(f"[!] 휠체어 보행 구간 DB 적재 건너뜀 (테이블 확인 필요): {e}")
    return True


def load_safety_etiquette_to_db(client: Client, guide_data: dict) -> bool:
    """안전 수칙, 에티켓, 준비물 및 탐방 팁 가이드 데이터를 Supabase RDB에 적재합니다."""
    if not guide_data:
        return False

    records = []

    # 1. safety_rules
    for item in guide_data.get("safety_rules", []):
        records.append({"category": "safety_rules", "content": item, "metadata": {}})

    # 2. etiquette
    for item in guide_data.get("etiquette", []):
        records.append({"category": "etiquette", "content": item, "metadata": {}})

    # 3. recommended_equipment
    for item in guide_data.get("recommended_equipment", []):
        content_text = f"{item.get('item', '')}: {item.get('description', '')}"
        records.append(
            {
                "category": "recommended_equipment",
                "content": content_text,
                "metadata": item,
            }
        )

    # 4. travel_planning_tips
    for item in guide_data.get("travel_planning_tips", []):
        records.append({"category": "travel_planning_tips", "content": item, "metadata": {}})

    if records:
        try:
            client.table("safety_etiquette_guide").upsert(records).execute()
        except Exception as e:
            print(f"[!] 안전 및 에티켓 가이드 DB 적재 건너뜀 (테이블 확인 필요): {e}")
    return True

