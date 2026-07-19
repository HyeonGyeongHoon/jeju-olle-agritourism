import re

def parse_courses(text: str) -> list[dict]:
    """영문 코스 헤더 패턴을 인식하여 코스별 데이터를 파싱합니다."""
    courses = []
    # TODO: 정규식을 활용한 Course \d{1,2}(-\d)? 형태 파싱
    return courses

def chunk_by_subtitle(course_text: str) -> list[str]:
    """소제목(―) 기호 기준으로 텍스트를 청킹합니다."""
    chunks = []
    # TODO: '―' 구분 기호 기준 청킹 알고리즘 구현
    return chunks
