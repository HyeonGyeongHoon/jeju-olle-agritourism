from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IntentCategory(str, Enum):
    """사전 의도 라우팅(Stage 0) 분류 카테고리입니다."""

    COURSE_INFO = "course_info"
    COURSE_RECOMMENDATION = "course_recommendation"
    OLLE_GENERAL_INFO = "olle_general_info"
    OTHER = "other"


class RouterResult(BaseModel):
    """사전 의도 라우터의 분류 결과 스키마입니다."""

    category: IntentCategory = Field(..., description="의도 라우팅 분류 결과 카테고리")
    target_course: str | None = Field(
        default=None, description="특정 코스 질문 시 추출된 코스명 (예: 1코스)"
    )
    reason: str = Field(default="", description="의도 분류 사유 설명")


class CourseSchema(BaseModel):
    """제주올레 코스 메타데이터 검증을 위한 Pydantic 모델입니다."""

    course_name: str = Field(..., description="코스명 (예: 1코스)")
    opening_date: str = Field(default="", description="개장일")
    total_distance_km: float = Field(..., ge=0.0, description="코스 총 거리 (km)")
    estimated_time_hours: float = Field(
        ..., ge=0.0, description="예상 소요 시간 (시간)"
    )
    estimated_time_text: str = Field(
        default="", description="예상 소요 시간 표기 (예: 4~5시간)"
    )
    difficulty: str = Field(default="중", description="난이도 (상, 중, 하)")
    course_description: str = Field(default="", description="코스 요약 설명")
    has_wheelchair_segment: str = Field(
        default="없음", description="휠체어 구간 존재 여부 (있음, 없음)"
    )
    start_point: str = Field(..., description="코스 시작점 명칭")
    end_point: str = Field(..., description="코스 종점 명칭")
    stamp_locations: str = Field(default="", description="스탬프 찍는 곳 명칭")
    lunch_info: str = Field(default="", description="식당 및 점심 정보")


class WheelchairSegmentSchema(BaseModel):
    """휠체어 보행 구간 검증을 위한 Pydantic 모델입니다."""

    segment_name: str = Field(
        ..., description="휠체어 구간 명칭 (예: 종달리 옛 소금밭 ~ 성산갑문 입구 구간)"
    )
    start_address: str = Field(..., description="휠체어 구간 시작점 지번/도로명 주소")
    distance_km: float = Field(..., ge=0.0, description="구간 거리 (km)")
    difficulty_level: Literal["상", "중", "하"] = Field(
        ..., description="난이도 등급 (상, 중, 하 중 택일)"
    )


class B2BQueryParams(BaseModel):
    """B2B 기획서 자연어 질의에서 추출한 핵심 파라미터 검증을 위한 Pydantic 모델입니다."""

    target_month: int | None = Field(
        default=None, ge=1, le=12, description="질의에서 언급된 방문 예정 월 (1~12)"
    )
    season: str | None = Field(
        default=None, description="질의에서 언급된 계절 표현 (예: 가을, 봄)"
    )
    key_item_or_crop: str | None = Field(
        default=None, description="질의의 핵심 매개 작물/테마 아이템 (예: 당근, 마늘, 밭담)"
    )
    preferred_location: str | None = Field(
        default=None, description="질의에서 언급된 선호 지역/코스 (예: 구좌읍, 1코스, 동부)"
    )
    concept_theme: str | None = Field(
        default=None, description="질의의 컨셉/테마 (예: 힐링, 평지 트레킹, 농가 체험)"
    )
    target_audience: str = Field(
        default="family",
        description="기획서의 주 타겟 고객층 (family, corporate, healing, senior, active)",
    )
    include_market_insights: bool = Field(
        default=True,
        description="제주관광공사 방문객 빅데이터(Market Insight) 섹션 포함 여부",
    )


class VisitorAnalyticsSchema(BaseModel):
    """제주관광공사 이동통신 빅데이터 기반 행정동별 방문객 통계 검증을 위한 Pydantic 모델입니다.
    성별/연령대 비율은 원본 PDF의 상위 랭킹 표에 등장하는 행정동에 대해서만 존재하므로
    (나머지는 그 달 데이터 자체가 없음) 모두 선택 필드입니다.
    """

    year_month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="예: 2026-05")
    region_dong: str = Field(..., description="행정동명 (예: 구좌읍, 애월읍)")
    total_visitors: int = Field(..., ge=0, description="당월 총 방문객 수")
    yoy_growth_rate: float | None = Field(default=None, description="전년 대비 증감률 (%)")
    female_ratio: float | None = Field(default=None, ge=0, le=100)
    male_ratio: float | None = Field(default=None, ge=0, le=100)
    youth_10s_ratio: float | None = Field(default=None, ge=0, le=100)
    young_2030_ratio: float | None = Field(default=None, ge=0, le=100)
    middle_4060_ratio: float | None = Field(default=None, ge=0, le=100)
    senior_70s_ratio: float | None = Field(default=None, ge=0, le=100)
    foreign_visitors: int | None = Field(default=None, ge=0)
