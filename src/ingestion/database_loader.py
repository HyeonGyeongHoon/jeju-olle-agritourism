import os
from supabase import create_client, Client

def get_supabase_client() -> Client:
    """Supabase 클라이언트 객체를 초기화하여 반환합니다."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    return create_client(url, key)

def load_courses_to_db(courses: list[dict]) -> bool:
    """코스 메타데이터 및 청크 데이터를 Supabase RDB & pgvector 에 적재합니다."""
    # TODO: OpenAI 임베딩 API 연계 및 DB Insert 구현
    return True

def load_wheelchair_segments_to_db(segments: list[dict]) -> bool:
    """휠체어 구간 정보를 Supabase RDB 에 적재합니다."""
    # TODO: DB Insert 구현
    return True
