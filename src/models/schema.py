from typing import Literal

from pydantic import BaseModel, Field


class CourseSchema(BaseModel):
    """제주올레 코스 메타데이터 검증을 위한 Pydantic 모델입니다."""

    course_name: str = Field(..., description="코스명 (예: 1코스)")
    opening_date: str = Field(default="", description="개장일")
    total_distance_km: float = Field(..., ge=0.0, description="코스 총 거리 (km)")
    estimated_time_hours: float = Field(
        ..., ge=0.0, description="예상 소요 시간 (시간)"
    )
    estimated_time_text: str = Field(default="", description="예상 소요 시간 표기 (예: 4~5시간)")
    difficulty: str = Field(default="중", description="난이도 (상, 중, 하)")
    course_description: str = Field(default="", description="코스 요약 설명")
    has_wheelchair_segment: str = Field(default="없음", description="휠체어 구간 존재 여부 (있음, 없음)")
    start_point: str = Field(..., description="코스 시작점 명칭")
    end_point: str = Field(..., description="코스 종점 명칭")
    stamp_locations: str = Field(default="", description="스탬프 찍는 곳 명칭")
    lunch_info: str = Field(default="", description="식당 및 점심 정보")


class CourseSubSegmentSchema(BaseModel):
    """코스 부분 탐방 큐레이션을 위한 세부 구간 Pydantic 모델입니다."""

    sub_segment_name: str = Field(
        ..., description="세부 구간 명칭 (예: 2-A구간 광치기~오조한도교)"
    )
    start_point: str = Field(..., description="구간 시작점 명칭")
    end_point: str = Field(..., description="구간 종점 명칭")
    distance_km: float = Field(..., ge=0.0, description="구간 거리 (km)")
    estimated_time_hours: float = Field(
        ..., ge=0.0, description="예상 소요 시간 (시간)"
    )
    description: str = Field(default="", description="구간 주요 풍경 및 특징 설명")


class WheelchairSegmentSchema(BaseModel):
    """휠체어 보행 구간 검증을 위한 Pydantic 모델입니다. 난이도 등급이 엄격하게 규제됩니다."""

    segment_name: str = Field(
        ..., description="휠체어 구간 명칭 (예: 종달리 옛 소금밭 ~ 성산갑문 입구 구간)"
    )
    start_address: str = Field(..., description="휠체어 구간 시작점 지번/도로명 주소")
    distance_km: float = Field(..., ge=0.0, description="구간 거리 (km)")
    difficulty_level: Literal["상", "중", "하"] = Field(
        ..., description="난이도 등급 (상, 중, 하 중 택일)"
    )


class SearchRequest(BaseModel):
    """하이브리드 RAG 검색 API (POST /api/v1/search) 요청 검증을 위한 모델입니다."""

    query: str = Field(..., min_length=1, description="사용자의 자연어 챗봇 질문")
    session_id: str = Field(
        ..., min_length=1, description="멀티턴 대화 유지용 세션 식별자 (필수)"
    )
    top_k: int = Field(
        default=3, ge=1, le=10, description="검색할 임베딩 청크 최대 개수"
    )


class WheelchairSourceSchema(BaseModel):
    """RAG 검색 결과 응답에 포함되는 개별 참조 데이터 스키마입니다."""

    course_name: str = Field(..., description="해당 코스 명칭")
    segment_name: str = Field(..., description="구간 명칭")
    start_address: str = Field(..., description="시작 주소")
    distance_km: float = Field(..., description="구간 거리 (km)")
    difficulty_level: Literal["상", "중", "하"] = Field(..., description="구간 난이도")


class SearchResponse(BaseModel):
    """하이브리드 RAG 검색 API 응답을 위한 Pydantic 모델입니다."""

    answer_context: str = Field(
        ..., description="최종 조립된 답변 텍스트 또는 프롬프트용 컨텍스트"
    )
    sources: list[WheelchairSourceSchema] = Field(
        default=[], description="답변에 참조된 원천 휠체어 데이터 목록"
    )
