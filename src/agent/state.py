"""LangGraph 상태 스키마와, 그 상태에 실려 흐르는 중첩 dict 들의 구조 정의.

(2026-07-26 타입 강화 — 런타임 동작 변경 없음)
예전에는 `AgentState` 의 거의 모든 필드가 `Dict[str, Any]` / `List[Dict[str, Any]]` 로만
선언되어 있어서, 어떤 노드가 어떤 키를 채우고 어떤 노드가 무엇을 읽는지가 코드 전체를
grep 해야만 알 수 있었습니다. 이 모듈은 그 dict 들을 각각 TypedDict 으로 명시합니다.

**TypedDict 은 런타임에 그냥 plain dict 입니다** — 이 파일은 문서/정적 분석용 선언일 뿐이고,
`b2b_params.get("preferred_location")` / `chunk["crops"]` / `"x" in state` 같은 기존 접근
방식은 한 글자도 바뀌지 않습니다. 노드가 반환하는 partial dict 도 그대로 유효합니다.

[필수(required) vs 선택(not-required) 표기 규약]
이 리포는 Python 3.10 에서 동작하고(`pyproject.toml` 의 `target-version = "py310"`),
`typing.NotRequired` 는 3.11 부터라서 쓸 수 없습니다. `typing_extensions` 는 pydantic 의
전이 의존성으로 설치돼 있긴 하지만 `requirements.txt` 에 명시된 패키지가 아니라, 새 의존성을
끌어들이지 않기 위해 **표준 라이브러리만으로 같은 것을 표현하는 관례**를 씁니다:

    class _XxxRequired(TypedDict):      # 항상 존재하는 키
        ...
    class XxxDict(_XxxRequired, total=False):   # 있을 수도 없을 수도 있는 키
        ...

즉 `_...Required` 베이스에 있는 키는 필수, `total=False` 서브클래스에서 추가된 키는
`NotRequired[...]` 와 동일한 의미입니다. 값 자체가 None 일 수 있는 것(nullable)은 키의
존재 여부와 다른 문제이므로 `Optional[...]` 로 따로 표기합니다.

각 TypedDict 독스트링에 "누가 채우는가 / 어디서 읽는가"를 파일 단위로 적어 두었습니다.
"""

from typing import Any, Dict, List, Optional, TypedDict, Union

# ---------------------------------------------------------------------------
# 1. 의도 분석 / 조건 파싱 결과
# ---------------------------------------------------------------------------


class HardConstraints(TypedDict, total=False):
    """완화가 허용되지 않는 하드 제약(현재는 휠체어 접근성 하나).

    채우는 곳: `parse_intent_node` (`nodes/intent.py`) — 정상 경로에서는 LLM JSON
    (`prompts/parse_intent.md` 추출 규칙 1)을 그대로 저장하고, LLM 파싱이 실패한 폴백
    경로에서는 원본 질의의 "휠체어" 키워드를 재확인해 직접 만듭니다(하드 제약을 조용히
    False 로 초기화하지 않기 위한 fail-closed 안전망).
    읽는 곳: `_execute_rdb_filtering` / `_describe_target_course_mismatch`
    (`services/db_service.py`, `nodes/retriever.py`), `rewrite_query_node`.

    전체가 total=False 인 이유: 소비부가 전부 `hard.get("wheelchair_required")` 형태로
    읽고, `rewrite_query_node` 는 `constraints.get("hard_constraints",
    {"wheelchair_required": False})` 로 기본값까지 두고 있습니다 — 즉 LLM 이 이 키를
    빼먹은 dict 도 실제로 흘러들어올 수 있다는 것을 코드가 이미 전제하고 있습니다.
    """

    wheelchair_required: bool


class ParsedConstraints(TypedDict, total=False):
    """RAG 검색용으로 정형화된 제약 조건(`AgentState.parsed_constraints`).

    채우는 곳: `parse_intent_node`, 그리고 재작성 루프의 `rewrite_query_node`.
    읽는 곳: `retrieve_rag_node`(`constraints.get("hard_constraints", {})` /
    `constraints.get("vector_query", state["query"])`), `quick_responder_node`
    (`constraints.get("vector_query")`), `rewrite_query_node`.

    total=False 인 이유: 두 키 모두 소비부가 기본값과 함께 `.get()` 으로 읽습니다.

    주의 — 여기 선언된 2개가 "소비되는 계약"의 전부이지만, 런타임의 dict 는 그보다 클 수
    있습니다. `parse_intent_node` 는 정상 경로에서 LLM 이 돌려준 JSON 전체를
    (`parsed = json.loads(cleaned)`) 이 필드에 그대로 저장하는데, 그 JSON 에는
    `prompts/parse_intent.md` 의 응답 포맷대로 `target_month`/`season`/
    `key_item_or_crop`/`preferred_location`/`market_location_query`/`concept_theme`/
    `target_audience`/`include_market_insights`/`strict_single_crop` 도 함께 들어 있습니다.
    다만 그 값들을 `parsed_constraints` 에서 읽는 코드는 **한 곳도 없습니다** — 전부
    Pydantic 검증을 통과한 `b2b_params`(아래 `B2BQueryParamsDict`) 쪽에서 읽습니다.
    그래서 여기서는 검증되지 않은 그 중복 사본을 계약으로 승격시키지 않고, 실제로 읽히는
    2개만 선언합니다(LLM 파싱 실패 폴백 경로가 만드는 dict 도 정확히 이 2개뿐입니다).
    """

    hard_constraints: HardConstraints
    vector_query: str


class MarketLocationQueryDict(TypedDict, total=False):
    """지역명 대신 방문객 통계 조건으로 지역을 역검색하려는 질의의 파라미터
    ("외국인 관광객이 많았던 지역" 등). `B2BQueryParamsDict.market_location_query` 에 실립니다.

    원본 모델: `src/models/schema.py` 의 `MarketLocationQuery`.
    읽는 곳: `resolve_market_location_node`(`nodes/intent.py`), `tool_agent_node`
    (`nodes/tools.py`).

    total=False 인 이유: 소비부가 `query_spec.get("metric")` / `market_query.get("year")`
    / `market_query.get("month")` / `market_query.get("direction") or "desc"` 처럼 전부
    `.get()` 으로 읽고, `tool_agent_node` 는 애초에 `... or {}` 로 빈 dict 를 허용합니다.
    실제로 `tests/test_market_location_resolver.py` 는 `{"metric": None}` 처럼 키가 하나만
    있는 dict 를 그대로 흘려보냅니다(Pydantic dump 경로는 4개를 모두 채우지만, 이 필드에
    도달하는 dict 가 항상 그 경로에서 온다는 보장은 없음).

    metric 이 `Optional[str]` 인 이유: 원본은 `MarketLocationMetric` Enum 이지만
    `B2BQueryParams(...).model_dump(mode="json")` 을 거치면서 평범한 문자열 값으로
    바뀝니다. 유효성은 `_MARKET_METRIC_LABELS` 화이트리스트 대조로 런타임에 확인합니다.
    """

    metric: Optional[str]
    year: Optional[int]
    month: Optional[int]
    direction: str  # "desc" | "asc"


class MarketLocationResolutionDict(TypedDict):
    """통계 조건으로 지역을 자동 선정한 **결과**와 그 근거(위 `MarketLocationQueryDict` 는
    "요청", 이쪽은 "해석 결과"입니다).

    채우는 곳: `resolve_market_location_node` 단 한 곳 — 다섯 키를 항상 함께 채우므로
    전부 필수입니다.
    읽는 곳: `quick_responder_node`(지역 자동 선정 근거 문구), `generate_report_node`
    (섹션 1 의 `location_resolution_str`; 이쪽은 `market_location_resolution["metric"]`
    처럼 대괄호로 직접 읽습니다).

    value 가 `Any` 인 이유: 어떤 metric 이었는지에 따라 정수(방문객 수)와 실수(비율/증감률)가
    모두 올 수 있고, 소비부는 이 값을 계산하지 않고 문자열로 포매팅만 합니다.
    """

    region_dong: str
    metric: str
    value: Any
    year_month: str
    direction: str  # "desc" | "asc"


class _B2BQueryParamsRequired(TypedDict):
    """`B2BQueryParamsDict` 의 필수 키 — `B2BQueryParams` 모델이 dump 시 항상 채우는 9개."""

    target_month: Optional[int]
    season: Optional[str]
    key_item_or_crop: Optional[str]
    preferred_location: Optional[str]
    market_location_query: Optional[MarketLocationQueryDict]
    concept_theme: Optional[str]
    target_audience: str
    include_market_insights: bool
    strict_single_crop: bool


class B2BQueryParamsDict(_B2BQueryParamsRequired, total=False):
    """B2B 기획서 생성용 핵심 파라미터(`AgentState.b2b_params`).

    채우는 곳: `parse_intent_node` 가 `B2BQueryParams(...).model_dump(mode="json")` 로
    만들고(그래서 위 9개 필드는 값이 None 일 수는 있어도 **키는 항상 존재**합니다),
    이후 `resolve_market_location_node` 와 `quick_responder_node` 가 일부 값을 보정해
    다시 씁니다.
    읽는 곳: 거의 모든 노드(`retrieve_rag_node`/`evaluate_safety_node`/
    `generate_report_node`/`check_quality_node`/`tool_agent_node`/
    `quick_responder_node`).

    `market_location_resolution` 만 total=False 인 이유는 **원본 Pydantic 모델에 없는
    필드**라서입니다 — `src/models/schema.py` 의 `B2BQueryParams` 에는 이 필드가 없고,
    `resolve_market_location_node` 가 통계 기반 지역 선정에 성공한 경우에만 dict 에
    런타임으로 끼워 넣습니다(그 노드가 아무 것도 하지 않는 일반 질의에서는 아예 없는 키).

    참고: 소비부는 위 9개 필수 필드도 관례적으로 `.get()` 으로 읽습니다. 이건 이 dict 가
    불완전할 수 있다는 뜻이 아니라, `AgentState.b2b_params` 자체가 `Optional` 이고 각
    노드가 `state.get("b2b_params") or {}` 로 None 을 빈 dict 로 흡수하기 때문입니다.
    """

    market_location_resolution: MarketLocationResolutionDict


class CourseMetaDict(TypedDict):
    """`target_course` 로 지목된 코스 한 건의 DB 실측 메타데이터(`AgentState.course_meta`).

    채우는 곳: `_fetch_course_meta_by_name`(`services/db_service.py`) →
    `quick_responder_node`.
    읽는 곳: `_build_course_meta_context_str`(`nodes/reporter.py`) 를 통해
    `quick_responder_node` 와 `tool_agent_node` 양쪽 프롬프트.

    키 목록은 `services/db_service.py` 의 `_COURSE_META_COLUMNS` select 절과 1:1 이라
    전부 필수입니다(select 한 컬럼은 값이 NULL 이어도 키 자체는 응답에 들어옵니다).
    Optional 여부는 `supabase/schema.sql` 의 `courses` DDL 을 그대로 반영했습니다 —
    `course_name`/`total_distance_km`/`estimated_time_hours`/`start_point`/`end_point`
    는 NOT NULL 이고, `crops`/`administrative_areas`/`estimated_time_text`/`difficulty`
    는 nullable 입니다. `_build_course_meta_context_str` 가 `value in (None, "")` 검사와
    `(course_meta.get("crops") or "")` 패턴으로 None 을 이미 방어하고 있는 것이 그 증거입니다.
    """

    course_name: str
    crops: Optional[str]
    administrative_areas: Optional[str]
    total_distance_km: float
    estimated_time_hours: float
    estimated_time_text: Optional[str]
    difficulty: Optional[str]
    start_point: str
    end_point: str


# ---------------------------------------------------------------------------
# 2. 날씨 및 안전 검증
# ---------------------------------------------------------------------------


class _WeatherInfoRequired(TypedDict):
    """`WeatherInfoDict` 의 필수 키 — `weather_client.py` 의 세 생성 지점이 모두 채우는 6개."""

    status: str  # "SAFE" | "WARNING" | "DANGER"
    temperature: float
    precipitation_mm: float
    wind_speed_ms: float
    warnings: List[str]
    description: str


class WeatherInfoDict(_WeatherInfoRequired, total=False):
    """기후 판단 결과(`AgentState.weather_info`). **실시간 기상 API 응답이 아닙니다** —
    월별 정적 계절 테이블(`get_seasonal_climate_note`)과 질의 텍스트 기반 LLM 판단
    (`assess_weather_risk_from_query`)의 결합 결과입니다(`agent/weather_client.py`).

    채우는 곳: `evaluate_safety_node`(`nodes/safety.py`).
    읽는 곳: `evaluate_safety_node` 자신(`safety_check` 조립), `generate_report_node`
    (섹션 4 기후 리스크).

    `guideline` 만 total=False 인 이유: 세 생성 경로 중 `get_seasonal_climate_note` 의
    정상 경로만 이 키를 넣습니다. 월 테이블에 없는 값이 들어온 `_safe_default` 경로와,
    `assess_weather_risk_from_query` 의 DANGER/SAFE 반환값에는 이 키가 아예 없습니다 —
    그래서 `evaluate_safety_node` 도 `weather.get("guideline") or "<기본 문구>"` 로 읽습니다.

    참고: `temperature`/`precipitation_mm`/`wind_speed_ms` 는 세 경로 모두 채우지만 실측이
    아닌 고정 상수이고, 현재 이 세 값을 읽는 소비부는 없습니다(그래도 항상 존재하는 키라서
    필수로 선언합니다).
    """

    guideline: str


class SafetyCheckDict(TypedDict):
    """기상/동선 리스크 진단 결과와 우회 지시(`AgentState.safety_check`).

    채우는 곳: `evaluate_safety_node` 한 곳 — 네 키를 항상 함께 채우므로 전부 필수입니다
    (`alternative_query_override` 는 먼저 None 으로 넣고 `reroute_required` 일 때만
    실제 문구로 덮어씁니다. 즉 "키가 없을 수 있는 것"이 아니라 "값이 None 일 수 있는 것"
    이라서 Optional 이지 total=False 가 아닙니다).
    읽는 곳: `retrieve_rag_node`(`reroute_required`/`alternative_query_override` 로
    vector_query 보정), `generate_report_node`(섹션 4 Plan B).
    """

    safety_status: str  # "SAFE" | "WARNING" | "DANGER"
    reason: str
    reroute_required: bool
    alternative_query_override: Optional[str]


# ---------------------------------------------------------------------------
# 3. 검색 결과
# ---------------------------------------------------------------------------


class RetrievedChunkDict(TypedDict):
    """pgvector 유사도 검색으로 찾은 코스 청크 + 그 코스의 메타데이터
    (`AgentState.retrieved_chunks` 의 원소).

    채우는 곳: `retrieve_rag_node`(`nodes/retriever.py`)의 `chunks_data.append({...})`
    한 곳 — 12개 키를 항상 함께 채우므로 전부 필수입니다.
    읽는 곳: `generate_report_node`(코스 컨텍스트/단가 산정/섹션 3~5),
    `check_quality_node`(검증 컨텍스트), `_crop_location_boost`,
    `_resolve_effective_crops`.

    `chunk_id`/`course_id`/`title`/`content`/`similarity` 는 `match_course_chunks` RPC
    응답에서, 나머지는 `courses` 행에서 옵니다.

    nullable 표기 근거:
    - `course_name`: `course_meta.get("course_name")` 에 기본값이 없어, 조인 대상 courses
      행을 못 찾아 `course_meta = {}` 가 된 경우 None 이 됩니다.
    - `crops`/`administrative_areas`/`estimated_time_text`/`difficulty`: `.get(key, "")`
      형태로 읽지만 이 기본값은 **키가 아예 없을 때만** 적용됩니다. 해당 컬럼들은
      `courses` DDL 상 nullable 이라 키는 있고 값만 NULL(None) 인 경우가 있어, 실제로
      None 이 그대로 실릴 수 있습니다(같은 함정 때문에 `total_distance_km`/
      `estimated_time_hours` 는 `float(... or 0.0)` 로 감싸 None 을 흡수하고 있고,
      그래서 그 둘만 non-Optional 입니다).
    """

    chunk_id: int
    course_id: int
    course_name: Optional[str]
    crops: Optional[str]
    administrative_areas: Optional[str]
    total_distance_km: float
    estimated_time_hours: float
    estimated_time_text: Optional[str]
    difficulty: Optional[str]
    title: str
    content: str
    similarity: float


class CultureChunkDict(TypedDict):
    """제주 밭담문화·작물 생육 지식 문서 청크(`AgentState.culture_chunks` 의 원소).

    채우는 곳: `_search_culture_knowledge`(`services/db_service.py`) —
    `match_culture_chunks` RPC 경로와 로컬 JSON 폴백(`_search_local_culture_docs`)
    경로가 **같은 11개 키**를
    채우므로 전부 필수입니다.
    읽는 곳: `_build_culture_context_str`(`nodes/reporter.py`),
    `generate_report_node`(Trust Tagging 출처 라벨), `_crop_label_matches`.

    `knowledge_id`/`category`/`target_crop`/`region_tag`/`active_months`/`season_stage`
    는 `supabase/schema.sql` 섹션 8-1 에서 나중에 ALTER 로 추가된 nullable 컬럼이고,
    실제로 ~26개 문서 중 `data/culture_knowledge/crop_seven_docs.json` 에서 온 7건에만
    값이 있습니다. 나머지 문서는 이 값들이 None 인 채로 흐릅니다.

    `active_months` 가 `Optional[List[int]]` 인 이유: DDL 이 `INTEGER[]` 이고,
    `_build_culture_context_str` 가 `target_month in active_months` 로 정수 멤버십을
    검사합니다.
    """

    id: int
    crop_name: Optional[str]
    title: str
    content: str
    similarity: float
    knowledge_id: Optional[str]
    category: Optional[str]
    target_crop: Optional[str]
    region_tag: Optional[str]
    active_months: Optional[List[int]]
    season_stage: Optional[str]


class SubSegmentDict(TypedDict):
    """최상위 매칭 코스의 세부 구간 한 건(`AgentState.sub_segments` 의 원소) —
    섹션 2 타임라인 표의 유일한 사실 근거입니다.

    채우는 곳: `retrieve_rag_node` 가 `course_sub_segments` 를
    `.select("sub_segment_name,distance_km")` 로 조회한 결과를 그대로 실어 보냅니다
    (가공하지 않으므로 select 한 두 컬럼이 곧 이 dict 의 키입니다).
    읽는 곳: `generate_report_node` 의 `segments_str` 조립
    (`s['sub_segment_name']` / `s['distance_km']` 대괄호 직접 접근).

    둘 다 `course_sub_segments` DDL 에서 NOT NULL 이라 필수 + non-Optional 입니다.
    """

    sub_segment_name: str
    distance_km: float


class _MarketInsightRequired(TypedDict):
    """`MarketInsightDict` 의 필수 키 — 소비부가 `.get()` 없이 대괄호로 직접 읽는 3개."""

    year_month: str  # "YYYY-MM"
    region_dong: str
    total_visitors: int


class MarketInsightDict(_MarketInsightRequired, total=False):
    """제주관광공사 이동통신 빅데이터 기반 행정동별 월간 방문객 통계 한 행
    (`AgentState.market_insight`). `visitor_analytics` 테이블의 `select("*")` 결과입니다.

    채우는 곳: `_fetch_market_insight` → `_query_visitor_analytics_row`
    (`services/db_service.py`) → `retrieve_rag_node` / `quick_responder_node`.
    읽는 곳: `_build_market_insight_summary_str`(`nodes/reporter.py`),
    `generate_report_node`(섹션 1 Market Insight + Trust Tagging), `check_quality_node`.

    필수/선택 구분 근거는 **DDL 이 아니라 소비부의 접근 방식**입니다.
    `year_month`/`region_dong`/`total_visitors` 는 `market_insight['region_dong']` 처럼
    대괄호로 직접 읽히므로(=없으면 KeyError) 필수이고, 나머지 지표는 전부
    `market_insight.get("yoy_growth_rate") is not None` 처럼 `.get()` + None 검사로
    읽힙니다. 그렇게 읽는 이유는 이 비율 지표들이 원본 PDF 의 상위 랭킹 표에 등장한
    행정동에만 존재해서 대부분 NULL 이기 때문입니다(`VisitorAnalyticsSchema` 독스트링
    참고). 값이 None 일 수 있는 것과 별개로, 테스트 픽스처처럼 키 자체를 생략한 dict 도
    이 경로에 그대로 흐를 수 있어 total=False 로 둡니다.

    `id`/`created_at` 은 `select("*")` 때문에 실려 오지만 읽는 코드가 없는 DB 부기용
    컬럼이라 역시 선택으로 둡니다.
    """

    id: int
    created_at: Optional[str]
    yoy_growth_rate: Optional[float]
    female_ratio: Optional[float]
    male_ratio: Optional[float]
    youth_10s_ratio: Optional[float]
    young_2030_ratio: Optional[float]
    middle_4060_ratio: Optional[float]
    senior_70s_ratio: Optional[float]
    foreign_visitors: Optional[int]


# ---------------------------------------------------------------------------
# 4. 답변 생성 및 로컬 추천
# ---------------------------------------------------------------------------


class RecommendationDict(TypedDict):
    """비짓제주 기반 로컬 상점 정보 한 건(`AgentState.recommendations` 의 원소).

    채우는 곳: `get_visit_jeju_recommendations`(`src/ingestion/visit_jeju_client.py`) —
    실 API 경로와 Mock 폴백 경로(`_get_mock_recommendations`)가 **같은 11개 키**를
    채우므로 전부 필수입니다(`source` 로 어느 경로였는지 구분).
    읽는 곳: `generate_report_node` — `rec.get("introduction")`(섹션 3 아이디어 재료)과
    `rec.get("source")`(섹션 5 Trust Tagging 에서 실 API/Mock 구분) 두 개만 읽습니다.

    매장명/주소/전화번호는 폐업·변경에 취약해 결과물에 노출하지 않는다는 정책이라
    dict 에는 실려 있지만 리포트에는 쓰이지 않습니다.
    """

    crop_tag: str
    title: str
    address: str
    road_address: str
    phone: str
    introduction: str
    latitude: float
    longitude: float
    administrative_area: str
    source: str  # "visitjeju_api" | "mock_db"
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------------
# 5. 품질 평가 및 도구 호출 루프
# ---------------------------------------------------------------------------


class QualityReportDict(TypedDict, total=False):
    """Self-RAG 자체 검증 결과(`AgentState.quality_report`).

    채우는 곳: `check_quality_node`(`nodes/quality.py`).
    읽는 곳: `should_continue`(`agent/graph.py`) 의 종료/재시도 분기,
    `rewrite_query_node`(`report.get("feedback", "")`), `tool_agent_node`(피드백 주입),
    `_score_to_stars`(Trust Tagging 별점).

    전체가 total=False 인 이유: 정상 경로의 `report` 는 **LLM 이 돌려준 JSON 을
    `json.loads` 한 결과 그대로**라서 세 키 중 어느 것도 존재가 보장되지 않습니다.
    코드도 그걸 전제로 동작합니다 — `should_continue` 는 `report.get("passed", False)`
    로 읽으면서 "passed 키가 없는 손상된 report 는 fail-closed 하게 실패로 간주"한다고
    명시하고 있고, `_score_to_stars` 호출부도 `report.get("score", 0.9)` /
    `report.get("passed", True)` 로 기본값을 둡니다. (반대로 `is_exit_early` 조기 통과
    분기와 LLM 실패 폴백 분기는 세 키를 모두 채운 dict 를 직접 만듭니다.)

    키 이름 주의: 피드백 문자열의 키는 `feedback` 입니다(`reason` 이나 `suggestions` 가
    아닙니다 — 검증 프롬프트의 응답 포맷과 모든 소비부가 `feedback` 으로 일치).
    """

    passed: bool
    score: float
    feedback: str


class ToolCallDict(TypedDict, total=False):
    """LLM 에이전트가 실행을 요청한 도구 호출 한 건(`AgentState.tool_calls` 의 원소).

    채우는 곳: `tool_agent_node`(`nodes/tools.py`) 가 `{"name": ..., "args": {...}}`
    형태로 만듭니다.
    읽는 곳: `tool_executor_node` — `should_call_tools`(`agent/graph.py`)는 존재 여부만 봅니다.

    전체가 total=False 인 이유: `tool_executor_node` 가 **두 가지 표기를 모두** 받도록
    되어 있습니다 —
        `call.get("name") or call.get("function", {}).get("name")`
        `call.get("args") or call.get("function", {}).get("arguments") or {}`
    즉 현재 생산자가 쓰는 평면 표기(`name`/`args`)와 OpenAI 스타일 중첩 표기
    (`function.name`/`function.arguments`)가 모두 유효하고, 어느 쪽이든 상대편 키는
    없습니다.

    `args` 가 `Union[Dict[str, Any], str]` 인 이유: 실행부가 위 식으로 인자를 합친 **뒤**
    `if isinstance(args, str): args = json.loads(args)` 를 거칩니다. 즉 (OpenAI 표기의
    `function.arguments` 처럼) JSON 문자열로 들어온 인자를 어느 출처든 허용합니다.
    현재 유일한 생산자인 `tool_agent_node` 는 항상 dict 로 넣습니다.
    """

    name: str
    args: Union[Dict[str, Any], str]
    function: Dict[str, Any]


class ToolOutputDict(TypedDict):
    """도구 실행 결과 한 건(`AgentState.tool_outputs` 의 원소).

    채우는 곳: `tool_executor_node`(`nodes/tools.py`) 의 `tool_outputs.append({...})`
    한 곳 — 세 키를 항상 함께 채우므로 전부 필수입니다.
    읽는 곳: `tool_agent_node`(`out['tool_name']` / `out['result']` 로 프롬프트 컨텍스트 조립).

    `tool_name` 이 Optional 인 이유: 호출 dict 에서 이름을 뽑는 식
    (`call.get("name") or call.get("function", {}).get("name")`)이 둘 다 없으면 None 을
    반환하고, 그 None 이 그대로 여기 실립니다(그 경우 `result` 에는 "[오류] 알 수 없는
    도구 호출" 문자열이 들어갑니다).
    `args` 는 실행부가 문자열 인자를 `json.loads` 로 파싱하거나 실패 시 `{}` 로
    대체하므로 항상 dict 입니다.
    """

    tool_name: Optional[str]
    args: Dict[str, Any]
    result: str


# ---------------------------------------------------------------------------
# 6. 그래프 상태 본체
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """LangGraph 의 에이전트 그래프 노드 간에 전달되는 상태 스키마입니다.

    각 노드는 이 스키마의 **부분 dict** 를 반환하고 LangGraph 가 그것을 병합합니다
    (그래서 노드 반환 타입은 `AgentState` 가 아니라 `Dict[str, Any]` 입니다).
    노드가 상태 변경 없이 빈 dict `{}` 를 반환할 수도 있는데(예:
    `resolve_market_location_node` 의 통계 조건 없는 일반 질의 — 가장 흔한 경우),
    그 경우 LangGraph `stream()` 은 해당 스텝의 이벤트 값을 `{}` 가 아니라 `None` 으로
    넘깁니다. `src/main.py` 의 `node_output = event[node_name] or {}` 가 그걸 흡수하는
    방어 코드이니, 빈 dict 를 반환할 수 있는 노드를 새로 추가할 때 반드시 확인하세요.
    """

    # 1. 입력 및 의도 분석 정보
    query: str
    parsed_constraints: Optional[ParsedConstraints]
    # IntentCategory enum 의 .value 문자열("course_recommendation"/"info_lookup"/"other").
    # enum 자체가 아니라 문자열이 실립니다 — 모든 라우팅 분기가
    # `state.get("intent_category") == IntentCategory.XXX.value` 로 비교합니다.
    intent_category: Optional[str]
    target_course: Optional[str]
    b2b_params: Optional[B2BQueryParamsDict]
    # target_course 로 조회한 코스의 DB 실측 메타데이터(거리/소요시간/난이도/시작·종점 등).
    # quick_responder_node 가 채우고 tool_agent_node 가 최종 답변의 근거로 재사용합니다 —
    # 최종 답변은 quick_responder 가 아니라 tool_agent 가 쓰는 경우가 많아서, 여기에 실어
    # 두지 않으면 코스 실측치가 최종 답변에 전달되지 않습니다.
    course_meta: Optional[CourseMetaDict]

    # 2. 날씨 및 안전 검증 정보
    weather_info: Optional[WeatherInfoDict]
    safety_check: Optional[SafetyCheckDict]

    # 3. 검색 및 완화 정보
    retrieved_chunks: List[RetrievedChunkDict]
    culture_chunks: List[CultureChunkDict]
    sub_segments: List[SubSegmentDict]
    fallback_applied: bool
    fallback_reason: Optional[str]
    market_insight: Optional[MarketInsightDict]
    # 무조건 반려(Fail-Fast) 정책 플래그(2026-07-25 정책 전환). retrieve_rag_node 의 세 필터
    # (target_course / preferred_location / key_item_or_crop) 중 하나라도 매칭 0건이면, 예전처럼
    # 그 조건을 해제하고 전체 검색으로 폴백하지 않고 그 자리에서 즉시 종료하면서 이 두 필드를
    # 채웁니다. route_after_retriever 가 is_exit_early 를 보고 report_generator 대신
    # quick_responder(반려 메시지 작성)로 제어를 넘기며, tool_agent_node/check_quality_node 도
    # 이 플래그를 확인해 각각 도구 재호출과 품질 재작성 루프를 건너뜁니다.
    is_exit_early: bool
    exit_reason: Optional[str]

    # 4. 답변 생성 및 로컬 추천 정보
    docent_answer: Optional[str]
    recommendations: List[RecommendationDict]
    final_response: Optional[str]

    # 5. 품질 평가 및 자율 순환 제어 정보
    quality_report: Optional[QualityReportDict]
    loop_count: int
    tool_calls: Optional[List[ToolCallDict]]
    tool_outputs: Optional[List[ToolOutputDict]]
    tool_depth: Optional[int]
