"""LangGraph 노드 패키지.

2026-07-26 이전에는 단일 파일 `src/agent/nodes.py`(1860줄) 였고, 이 패키지는 그 파일을
역할별 6개 서브모듈로 물리적으로 나눈 것입니다(로직 변경 없는 순수 코드 이동).

    intent.py     classify_intent_node / parse_intent_node /
                  resolve_market_location_node
    safety.py     evaluate_safety_node
    retriever.py  retrieve_rag_node + 검색-정책 헬퍼(_filter_course_ids_by_* 등)
    reporter.py   generate_report_node / quick_responder_node + 프롬프트 컨텍스트 헬퍼
    quality.py    check_quality_node / rewrite_query_node
    tools.py      tool_agent_node / tool_executor_node

[이 __init__.py 가 하는 일 — 단순 편의가 아니라 호환성 계약]
분할 이전에는 노드 함수뿐 아니라 이 노드들이 쓰는 모든 헬퍼/클라이언트
(`get_chat_completion`, `get_supabase_client`, `_fetch_market_insight`, ...)가 전부
`src.agent.nodes` 한 모듈의 전역이었습니다. 그래서
  - `src/agent/graph.py`, `src/agent/tools.py` 등 호출부가
    `from src.agent.nodes import X` 로 가져다 썼고,
  - 테스트가 `patch.object(nodes, "X", ...)` 로 목킹할 수 있었습니다.
분할 후에도 **그 네임스페이스를 한 글자도 바꾸지 않기 위해** 아래에서 옛 nodes.py 의
모듈 전역과 동일한 이름들을 전부 이 패키지에 다시 노출합니다. 각 서브모듈의 노드 함수는
이 이름들을 모듈 상단이 아니라 함수 본문 안에서 `from src.agent.nodes import ...` 로
가져오므로(각 서브모듈 상단의 "로컬 임포트 규약" 주석 참고), 호출 시점에 여기 바인딩된
값(=테스트가 갈아끼운 목)이 실제로 사용됩니다.
"""

# --- 외부 의존성 재노출 (옛 nodes.py 모듈 전역과 동일) ---
from src.agent.llm_client import get_chat_completion
from src.agent.router import route_intent
from src.agent.state import AgentState
from src.agent.weather_client import (
    assess_weather_risk_from_query,
    get_seasonal_climate_note,
)
from src.ingestion.database_loader import get_solar_embedding, get_supabase_client
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations
from src.models.schema import B2BQueryParams, IntentCategory
from src.services.db_service import (
    _ADMIN_DONG_TO_LEGAL_DONGS,
    _DIFFICULTY_ORDER,
    _execute_rdb_filtering,
    _fetch_course_meta_by_name,
    _fetch_market_insight,
    _get_known_crop_tags,
    _get_latest_available_year_month,
    _get_olle_relevant_admin_dongs,
    _looks_like_course_name,
    _normalize_admin_tier_name,
    _normalize_allowed_difficulties,
    _resolve_stats_region_from_areas,
    _search_culture_knowledge,
)
from src.services.price_estimation_service import (
    _build_price_breakdown_str,
    _estimate_price_range,
    _resolve_effective_crops,
)

# isort: split
# --- 서브모듈의 노드 함수 및 헬퍼 재노출 ---
# (위 "외부 의존성" 블록과 의도적으로 분리해 둡니다 — 어떤 이름이 외부에서 온
#  재노출이고 어떤 이름이 이 패키지 서브모듈 소유인지 한눈에 구분되도록.
#  `# isort: split` 은 두 블록을 하나로 합치려는 규칙만 끄는 표시입니다.)
from src.agent.nodes.intent import (
    classify_intent_node,
    parse_intent_node,
    resolve_market_location_node,
)
from src.agent.nodes.quality import (
    _QUALITY_COMMENT_PLACEHOLDER,
    _SELF_RAG_STARS_PLACEHOLDER,
    _build_quality_comment,
    _score_to_stars,
    check_quality_node,
    rewrite_query_node,
)
from src.agent.nodes.reporter import (
    _COURSE_META_DISPLAY_FIELDS,
    _MARKET_METRIC_LABELS,
    _OUT_OF_SCOPE_DECLINE_MSG,
    _build_course_meta_context_str,
    _build_culture_context_str,
    _build_market_insight_summary_str,
    generate_report_node,
    quick_responder_node,
)
from src.agent.nodes.retriever import (
    _LOCATION_SELECT_COLS,
    _LOCATION_SELECT_COLS_LEGACY,
    _build_fail_fast_result,
    _course_row_matches_location,
    _crop_location_boost,
    _describe_hard_constraint_zero_match,
    _describe_target_course_mismatch,
    _filter_course_ids_by_crop,
    _filter_course_ids_by_location,
    _filter_course_ids_by_target_course,
    _format_difficulty_labels,
    _split_comma_tokens,
    retrieve_rag_node,
)
from src.agent.nodes.safety import evaluate_safety_node
from src.agent.nodes.tools import tool_agent_node, tool_executor_node

__all__ = [
    # 11개 노드 함수 (graph.py 가 가져다 쓰는 공개 API)
    "classify_intent_node",
    "parse_intent_node",
    "resolve_market_location_node",
    "evaluate_safety_node",
    "retrieve_rag_node",
    "generate_report_node",
    "quick_responder_node",
    "tool_agent_node",
    "tool_executor_node",
    "check_quality_node",
    "rewrite_query_node",
    # 옛 nodes.py 전역 네임스페이스 호환용 재노출 (위 독스트링 참고).
    # 목킹 대상이거나 다른 모듈/서브모듈이 참조하므로 제거하면 안 됩니다.
    "get_chat_completion",
    "route_intent",
    "assess_weather_risk_from_query",
    "get_seasonal_climate_note",
    "get_solar_embedding",
    "get_supabase_client",
    "get_visit_jeju_recommendations",
    "B2BQueryParams",
    "IntentCategory",
    "AgentState",
    "_ADMIN_DONG_TO_LEGAL_DONGS",
    "_DIFFICULTY_ORDER",
    "_execute_rdb_filtering",
    "_fetch_course_meta_by_name",
    "_fetch_market_insight",
    "_get_known_crop_tags",
    "_get_latest_available_year_month",
    "_get_olle_relevant_admin_dongs",
    "_looks_like_course_name",
    "_normalize_admin_tier_name",
    "_normalize_allowed_difficulties",
    "_resolve_stats_region_from_areas",
    "_search_culture_knowledge",
    "_build_price_breakdown_str",
    "_estimate_price_range",
    "_resolve_effective_crops",
    "_LOCATION_SELECT_COLS",
    "_LOCATION_SELECT_COLS_LEGACY",
    "_build_fail_fast_result",
    "_course_row_matches_location",
    "_crop_location_boost",
    "_describe_hard_constraint_zero_match",
    "_describe_target_course_mismatch",
    "_filter_course_ids_by_crop",
    "_filter_course_ids_by_location",
    "_filter_course_ids_by_target_course",
    "_format_difficulty_labels",
    "_split_comma_tokens",
    "_COURSE_META_DISPLAY_FIELDS",
    "_MARKET_METRIC_LABELS",
    "_OUT_OF_SCOPE_DECLINE_MSG",
    "_build_course_meta_context_str",
    "_build_culture_context_str",
    "_build_market_insight_summary_str",
    "_SELF_RAG_STARS_PLACEHOLDER",
    "_score_to_stars",
    "_QUALITY_COMMENT_PLACEHOLDER",
    "_build_quality_comment",
]
