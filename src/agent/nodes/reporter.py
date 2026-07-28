"""기획서 작성(report_generator)과 정보성 답변(quick_responder) 노드,
그리고 이 두 노드 전용 프롬프트 컨텍스트 조립 헬퍼.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Dict, List

from src.agent.prompts.loader import load_prompt
from src.agent.state import AgentState
from src.models.schema import IntentCategory

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


_MARKET_METRIC_LABELS = {
    "total_visitors": "총 방문객 수",
    "yoy_growth_rate": "전년 대비 증감률",
    "female_ratio": "여성 방문객 비중",
    "male_ratio": "남성 방문객 비중",
    "youth_10s_ratio": "10대 이하 비중",
    "young_2030_ratio": "2030대 비중",
    "middle_4060_ratio": "40~60대 비중",
    "senior_70s_ratio": "70대 이상 비중",
    "foreign_visitors": "외국인 방문객 수",
}


# 코스 의견/적합성 답변에 인용할 실측 수치의 표시 라벨 (컬럼 → 라벨, 단위)
_COURSE_META_DISPLAY_FIELDS = (
    ("total_distance_km", "총 거리", "km"),
    ("estimated_time_hours", "예상 소요시간", "시간"),
    ("estimated_time_text", "소요시간 안내", ""),
    ("difficulty", "난이도", ""),
    ("start_point", "시작점", ""),
    ("end_point", "종점", ""),
)


def _josa_ro(word: str) -> str:
    """한국어 조사 '로'/'으로'를 마지막 글자의 받침 유무로 선택합니다. 받침이 없거나
    ('으로'가 아니라) 'ㄹ' 받침이면 '로', 그 외 받침이 있으면 '으로'(예: "코스"→"로",
    "동선"→"으로", "서울"→"로"). 한글이 아닌 문자로 끝나면 안전하게 '으로'를 반환합니다.
    """
    word = word.strip()
    if not word or not ("가" <= word[-1] <= "힣"):
        return "으로"
    jongseong = (ord(word[-1]) - 0xAC00) % 28
    return "로" if jongseong in (0, 8) else "으로"


def _build_course_meta_context_str(course_meta: Dict[str, Any] | None, include_crops: bool = True) -> str:
    """코스 메타데이터를 LLM 프롬프트에 넣을 "라벨: 값" 목록 문자열로 만듭니다. 값이 없는
    필드는 아예 넣지 않아, LLM 이 빈 값을 보고 추측으로 메우지 않도록 합니다.
    include_crops=False 면 대표 재배 작물 항목을 제외하며, 작물·농업 키워드가 없는 순수
    코스 스펙 질의 시 LLM 이 crops 필드를 보고 자체적으로 제철·체험 정보를 생성하는 것을 방지합니다.
    """
    if not course_meta:
        return ""
    lines = []
    for column, label, unit in _COURSE_META_DISPLAY_FIELDS:
        value = course_meta.get(column)
        if value in (None, ""):
            continue
        lines.append(f"- {label}: {value}{unit}")
    if include_crops:
        crops = (course_meta.get("crops") or "").strip()
        if crops:
            lines.append(f"- 대표 재배 작물: {crops}")
    areas = (course_meta.get("administrative_areas") or "").strip()
    if areas:
        lines.append(f"- 경유 행정구역: {areas}")
    return "\n".join(lines)


def _build_culture_context_str(culture_chunks: List[Dict[str, Any]], target_month: int) -> str:
    """밭담문화·작물 생육 지식 컨텍스트 문자열을 만듭니다. crop_seven_docs.json 계열 문서
    (target_crop/region_tag/active_months/season_stage 보유)는 방문 예정월과 활동월을 대조해
    제철 여부를 함께 명시함으로써, LLM 이 제철이 아닌 작물을 "지금 한창"인 것처럼 서술하지
    않도록 합니다. 나머지 문서(신규 필드 없음)는 title/content만 표기. generate_report_node와
    quick_responder_node가 공통으로 사용합니다.
    """
    culture_context_str = ""
    for i, cc in enumerate(culture_chunks):
        meta_parts = []
        crop_label = cc.get("target_crop") or cc.get("crop_name")
        if crop_label:
            meta_parts.append(f"작물: {crop_label}")
        if cc.get("region_tag"):
            meta_parts.append(f"주산지: {cc['region_tag']}")
        if cc.get("season_stage"):
            meta_parts.append(f"생육 단계: {cc['season_stage']}")
        active_months = cc.get("active_months")
        if active_months:
            in_season = target_month in active_months
            months_str = ",".join(str(m) for m in sorted(active_months))
            meta_parts.append(
                f"활동월: {months_str}월 (방문 예정월 {target_month}월 기준 "
                f"{'제철 - 실제로 볼 수 있는 시기' if in_season else '제철 아님 - 다른 시기의 경관/서사로 대체 필요'})"
            )
        meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""
        culture_context_str += f"\n[문화지식 {i+1}] {cc['title']}{meta_str}:\n{cc['content']}\n"
    return culture_context_str.replace("~", "～")


def _build_market_insight_summary_str(market_insight: Dict[str, Any] | None) -> str:
    """관광 방문객 통계(visitor_analytics) 한 행을 사람이 읽는 요약 문자열로 변환합니다.
    quick_responder_node와 check_quality_node(코스 청크가 없는 경로)가 공통으로 사용합니다.
    데이터가 없으면 빈 문자열을 반환합니다.
    """
    if not market_insight:
        return ""
    parts = [
        f"{market_insight['region_dong']} {market_insight['year_month']} "
        f"방문객 {market_insight['total_visitors']:,}명"
    ]
    if market_insight.get("yoy_growth_rate") is not None:
        parts.append(f"전년 대비 {market_insight['yoy_growth_rate']}%")
    if market_insight.get("foreign_visitors") is not None:
        parts.append(f"외국인 방문객 {market_insight['foreign_visitors']:,}명")
    if market_insight.get("female_ratio") is not None:
        parts.append(f"여성 비중 {market_insight['female_ratio']}%")
    if market_insight.get("young_2030_ratio") is not None:
        parts.append(f"2030 비중 {market_insight['young_2030_ratio']}%")
    if market_insight.get("middle_4060_ratio") is not None:
        parts.append(f"40～60대 비중 {market_insight['middle_4060_ratio']}%")
    if market_insight.get("senior_70s_ratio") is not None:
        parts.append(f"70대 이상 비중 {market_insight['senior_70s_ratio']}%")
    return "📊 " + ", ".join(parts)


_OUT_OF_SCOPE_DECLINE_MSG = (
    "죄송하지만 이 질문에는 답변드리기 어렵습니다. 이 서비스는 방문 시기·작물·지역·테마 "
    "조건에 맞춰 제주 올레 코스의 B2B 관광 상품 기획서를 작성해 드리는 전용 서비스로, "
    "개별 코스 추천이나 날씨·맛집 같은 일반 정보 안내는 제공하지 않습니다. "
    "'~코스로 기획서 만들어줘'처럼 요청해 주시면 도와드릴 수 있습니다."
)


def _build_safety_guide_context_str(chunks: list) -> str:
    """safety_etiquette_guide 테이블의 안전 수칙 데이터를 카테고리별로 구조화한 텍스트로 빌드합니다."""
    if not chunks:
        return ""
    cat_map = {
        "safety_rules": "🚨 [안전 수칙]",
        "etiquette": "💚 [친환경 에티켓]",
        "recommended_equipment": "🎒 [추천 장비/준비물]",
        "travel_planning_tips": "📅 [탐방 계획 팁]"
    }
    lines = []
    for cat_key, cat_name in cat_map.items():
        items = [c["content"] for c in chunks if c["category"] == cat_key]
        if items:
            lines.append(f"{cat_name}:")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
    return "\n".join(lines).strip()


def quick_responder_node(state: AgentState) -> Dict[str, Any]:
    """기획서 생성 없이, 제주 밭담문화·작물 생육 지식과 관광 방문객 통계만 검색해 간결한 정보성
    답변을 빠르게 제공하는 Quick Responder 노드입니다. course_recommendation 을 제외한
    모든 의도(info_lookup/other)의 공통 경로로,
    safety_evaluator/코스 검색/report_generator 를 전부 건너뜁니다.
    retrieved_chunks 는 건드리지 않고 빈 상태 그대로 둡니다 — 코스 검색을 하지 않았다는 사실 자체가
    check_quality_node 등 하류 노드에 "이 경로는 코스 기획서가 아니다"를 알리는 신호로 쓰입니다.
    **(2026-07-25 추가)** `other`로 분류된 질의는 두 가지입니다 — 제주 올레와 아예 무관한 질문
    (예: "서울 맛집 추천해줘")과, 제주 올레 관련이지만 기획서가 아니라 단순 코스 "추천"을
    요청하는 질문(예: "당근 코스 추천해줘"). 이 서비스는 코스 추천 자체를 제공하지 않으므로
    (사용자 요청: "코스 추천은 하지 않습니다"라고 명확히 예외 처리), 두 경우 모두 문화·작물
    지식이나 통계를 검색해 대신 답하지 않고 서비스 범위를 안내한 뒤 즉시 종료합니다.
    **(2026-07-25 추가 — 무조건 반려/Fail-Fast)** retrieve_rag_node 가 DB 매칭 0건으로 검색을
    중단했으면(is_exit_early) route_after_retriever 가 report_generator 대신 이 노드로 제어를
    넘깁니다. 그 경우 이 노드는 반려 사유만 그대로 최종 답변으로 돌려주고, culture_crop_knowledge/
    visitor_analytics 조회는 한 번도 하지 않습니다 — 반려 사유와 무관한 문화·통계 내용을 덧붙이면
    "코스는 못 찾았지만 대신 이런 정보가 있다"는 식의 사실상 대체 추천이 되고, quality_checker 가
    그 무관한 컨텍스트와 반려 메시지를 대조하며 재작성 루프를 돌게 됩니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _fetch_course_meta_by_name,
        _fetch_market_insight,
        _looks_like_course_name,
        _resolve_stats_region_from_areas,
        _search_culture_knowledge,
        get_supabase_client,
    )

    # pyrefly: ignore [missing-import]
    from src.services.db_service import _fetch_safety_etiquette_guide

    if state.get("is_exit_early"):
        reason = state.get("exit_reason") or "요청하신 조건에 맞는 코스를 찾지 못했습니다."
        # 문구는 generate_report_node 의 `if not chunks:` 반려 분기와 동일한 형태로 통일합니다.
        msg = f"요청하신 조건으로는 기획서를 작성할 수 없습니다. {reason}"
        return {"docent_answer": msg, "final_response": msg, "skip_quality_check": True}

    if state.get("intent_category") == IntentCategory.OTHER.value:
        return {
            "culture_chunks": [],
            "market_insight": None,
            "docent_answer": _OUT_OF_SCOPE_DECLINE_MSG,
            "final_response": _OUT_OF_SCOPE_DECLINE_MSG,
            "skip_quality_check": True,
        }

    query = state["query"]
    constraints = state.get("parsed_constraints") or {}
    b2b_params = state.get("b2b_params") or {}
    target_course = state.get("target_course")
    key_item_or_crop = b2b_params.get("key_item_or_crop")
    preferred_location = b2b_params.get("preferred_location")
    target_month = b2b_params.get("target_month") or date.today().month
    include_market_insights = b2b_params.get("include_market_insights", True)
    location_resolution = b2b_params.get("market_location_resolution")
    fallback_query = constraints.get("vector_query") or query

    # 질문에 통계 관련 키워드가 포함되었는지 확인합니다 (지역 정밀화 및 통계 조회 판단용)
    stats_keywords = ["통계", "방문객", "인구", "사람", "수치", "방문", "수", "인원", "트래픽"]
    is_stats_related = any(kw in query for kw in stats_keywords)

    client = get_supabase_client()

    # "OO코스 알려줘" 처럼 특정 코스명이 언급된 질의는, 파서가 key_item_or_crop/
    # preferred_location 을 못 채웠어도 그 코스의 실제 작물/지역으로 검색 조건을 보완합니다
    # (이전엔 target_course 가 아예 무시되어 코스명을 특정해도 일반 검색과 동일하게 동작했음).
    course_meta = _fetch_course_meta_by_name(client, target_course) if target_course else None

    # preferred_location 에 지역명이 아니라 코스명이 들어온 경우(intent_parser 가 실제로 그렇게
    # 채웁니다 — 2026-07-25 라이브 QA에서 "1코스 괜찮을지 추천해줄래?" → preferred_location=
    # '1코스' 확인)를 지역 필드로서 무효 처리합니다. 예전엔 이 값이 truthy 라는 이유만으로 아래
    # 보완 로직을 건너뛰어, 코스명이 그대로 tool_agent 의 통계 조회 지역 인자로 전달됐고,
    # 도구가 반환한 "조회 가능한 지역: ..." 목록을 LLM 이 그대로 "대신 이 지역들을 추천드릴 수
    # 있어요"로 노출해 — 라우팅 규칙으로 막으려던 "다른 코스/지역 추천"이 이 경로로 재발했습니다.
    if _looks_like_course_name(preferred_location):
        preferred_location = None

    # 질문에 작물·농업·문화 관련 키워드가 있을 때만 문화지식 검색을 허용합니다.
    # 코스 메타에서 작물명을 자동 보완하는 로직도 이 조건 내에서만 동작해야 합니다.
    # "소요시간 알려줘"처럼 순수 코스 스펙 질의 시에는 작물명이 조용히 채워져
    # should_search_culture=True 가 되어 무관한 농작물 문화지식이 주입되는 것을 방지합니다.
    agro_query_keywords = [
        "작물", "감귤", "감자", "당근", "마늘", "양파", "무", "파", "배추", "보리", "밭담",
        "농가", "상생", "영농", "수확", "재배", "파종", "체험", "제철", "로컬", "농업"
    ]
    is_query_agro_related = any(kw in query for kw in agro_query_keywords)

    if course_meta:
        # 질문에 작물/농업 키워드가 있을 때만 코스 메타의 재배작물로 key_item_or_crop 을 보완합니다.
        # 순수 코스 스펙(소요시간/난이도 등) 질의에서는 작물명 자동 채움을 생략합니다.
        if not key_item_or_crop and is_query_agro_related:
            key_item_or_crop = (course_meta.get("crops") or "").split(",")[0].strip() or None
        # 질문에 통계 관련 키워드가 포함되었을 때만 지역 매핑(법정동-행정동 변환) 작업을 수행합니다.
        # 단순 코스 스펙(소요시간/난이도 등) 질문 시에는 이 무거운 DB 조회를 건너뜁니다 (초경량 Fast Path).
        if not preferred_location and is_stats_related:
            # administrative_areas 는 법정리/법정동 단위라 visitor_analytics.region_dong
            # (행정동/읍·면)과 계층이 달라 그대로 쓰면 통계 조회가 또 실패합니다. 통계 조회가
            # 가능한 행정동/읍·면으로 변환해서만 채우고, 변환할 수 없으면 비워 둡니다.
            preferred_location = _resolve_stats_region_from_areas(
                client, course_meta.get("administrative_areas")
            )

    # 질문에 작물명/테마(key_item_or_crop)도 컨셉 테마(concept_theme)도 없으면 문화지식 검색
    # 자체를 실행하지 않습니다. _search_culture_knowledge 는 key_item_or_crop 이 없으면
    # fallback_query(원본 질의 텍스트)를 그대로 임베딩해 유사도 0.1의 낮은 임계치로 검색하므로,
    # "OO동 방문객 수는?"처럼 순수 통계 질의에도 유채꽃/양파 등 무관한 문화·작물 문서가 섞여
    # 나올 수 있었습니다(라이브 QA 확인). 작물/테마 신호가 전혀 없는 질의는 통계 정보만으로
    # 간결하게 답해야 하므로, 이 경우 검색 자체를 생략합니다.
    concept_theme = b2b_params.get("concept_theme")
    # concept_theme 이 영농, 밭담, 수확 등 농가 상생 테마와 관련된 경우에만 문화지식을 검색하도록 제한합니다.
    # 단순 안전 수칙, 준비물 등 일반 질문에서 무관한 작물 정보가 매칭되어 오염되는 것을 방지합니다.
    is_agro_theme = False
    if concept_theme:
        agro_keywords = ["밭담", "농가", "상생", "영농", "수확", "재배", "파종", "작물", "체험", "로컬"]
        is_agro_theme = any(keyword in concept_theme for keyword in agro_keywords)

    should_search_culture = bool(key_item_or_crop) or is_agro_theme
    culture_chunks = (
        _search_culture_knowledge(client, key_item_or_crop, fallback_query)
        if should_search_culture
        else []
    )

    # 질문에 안전/준비물/에티켓 관련 키워드가 포함되면 안전 및 에티켓 가이드를 함께 불러옵니다.
    safety_keywords = ["안전", "수칙", "에티켓", "준비물", "장비", "주의", "가이드", "팁", "날씨", "기상"]
    is_safety_related = any(keyword in query for keyword in safety_keywords)
    safety_guide_chunks = _fetch_safety_etiquette_guide(client) if is_safety_related else []

    market_insight = None
    if include_market_insights and is_stats_related:
        # target_month(위에서 today() 로 기본값 처리된 값)를 그대로 넘기지 않고 질의가 실제로
        # 지정한 월(b2b_params 원본, 없으면 None)만 넘깁니다 — "최근 방문객 수는?"처럼 월이
        # 없는 질의에서 "오늘 날짜의 달"을 강제하면, DB 적재 범위가 이번 달까지 아닐 때 실제로는
        # 최근 데이터가 있는데도 조회가 0건으로 실패합니다. None 이면 _fetch_market_insight 가
        # 월 필터 없이 해당 지역의 최신 데이터를 가져옵니다.
        market_insight = _fetch_market_insight(client, preferred_location, b2b_params.get("target_month"))

    # 코스가 지목된 질의는 코스명 라벨만 넘기지 않고 DB 실측치(거리/소요시간/난이도/시작·종점)를
    # 함께 넘깁니다. 라벨만 넘겼을 때 LLM 이 "비교적 평탄해 초보자도 좋다"처럼 DB 근거 없는
    # 추측으로 적합성을 단정하는 문제가 라이브 QA에서 확인됐습니다(2026-07-25).
    course_note = ""
    if target_course:
        course_note = f"[대상 코스] 이 질문은 '{target_course}' 코스에 대한 것입니다."
        # 작물·농업 관련 질문이 아닌 경우 코스 메타에서 crops 필드를 제외합니다.
        # LLM 이 crops 필드를 보고 자체적으로 제철·체험 정보를 생성하는 오염을 차단합니다.
        course_meta_context_str = _build_course_meta_context_str(course_meta, include_crops=is_query_agro_related)
        if course_meta_context_str:
            course_note += (
                f"\n[대상 코스 DB 실측 메타데이터]\n{course_meta_context_str}"
            )
        else:
            course_note += "\n[대상 코스 DB 실측 메타데이터] (조회하지 못했습니다.)"
        course_note += "\n\n"

    # _build_safety_guide_context_str 는 빈 리스트를 받으면 "" 를 반환하므로 조건문 없이 항상
    # 호출해도 안전합니다 - 이후 섹션 4 조립부의 참조가 possibly-unbound 가 되는 것도 방지합니다.
    safety_guide_context_str = _build_safety_guide_context_str(safety_guide_chunks)

    answer_parts = []

    # 1. 대상 코스 상세 스펙 및 B2B 가이드 조립
    if target_course and course_meta:
        c_meta = course_meta
        course_name = c_meta.get("course_name")
        total_distance = c_meta.get("total_distance_km")
        estimated_time = c_meta.get("estimated_time_text") or (
            f"{c_meta.get('estimated_time_hours')}시간" if c_meta.get("estimated_time_hours") is not None else None
        )
        difficulty = c_meta.get("difficulty")
        start_point = c_meta.get("start_point")
        end_point = c_meta.get("end_point")
        admin_areas = c_meta.get("administrative_areas") or ""
        crops = c_meta.get("crops") or ""

        # 실제 값이 존재하는 필드만 명사구 불릿 형태로 조립합니다.
        specs = []
        if course_name:
            specs.append(f"- 코스명: {course_name}")
        if total_distance is not None:
            specs.append(f"- 총 거리: {total_distance}km")
        if estimated_time:
            specs.append(f"- 예상 소요시간: {estimated_time} 내외")
        if difficulty:
            specs.append(f"- 난이도: {difficulty}")
        if start_point:
            specs.append(f"- 시작점: {start_point}")
        if end_point:
            specs.append(f"- 종점: {end_point}")
        if admin_areas:
            specs.append(f"- 경유 행정구역: {admin_areas}")

        specs_str = "\n".join(specs)
        course_str = (
            f"[대상 코스 상세 스펙]\n"
            f"{specs_str}"
        )
        
        guidelines = []
        if estimated_time:
            guidelines.append(f"- {course_name} 의 예상 소요시간({estimated_time}) 및 지형 특성을 고려하여, 4시간 내외 빠른 이동 그룹과 5시간 이상 경관 체험 그룹을 구분한 맞춤형 타임테이블 수립 권장.")
        if difficulty:
            guidelines.append(f"- 난이도 '{difficulty}' 및 도보 환경을 고려하여 단체 탐방객 대상 체력 분배 가이드라인 마련 및 코스 내 적정 휴식 지점(쉼터 등) 배치 계획 수립 필요.")
        if crops and is_query_agro_related:
            guidelines.append(f"- 대표 재배 작물인 '{crops}' 과 연계한 제철 농가 체험(수확/시식 등) 프로그램 연계 기획 시, 해당 작물의 생육 시기 및 수확 월(시즌 가이드) 사전 확인 필수.")

        if guidelines:
            course_str += "\n\n[B2B 관광 상품 설계 지침]\n" + "\n".join(guidelines)
            
        answer_parts.append(course_str)

    # 2. 관광 방문객 통계 분석 조립
    if market_insight:
        m_in = market_insight
        region = m_in.get("region_dong")
        ym = m_in.get("year_month")
        total_v = m_in.get("total_visitors") or 0
        yoy = m_in.get("yoy_growth_rate")
        female = m_in.get("female_ratio")
        male = m_in.get("male_ratio")

        stats_str = (
            f"[방문객 빅데이터 및 트래픽 분석]\n"
            f"- 기준 지역 및 월: {region} ({ym} 기준)\n"
            f"- 월간 총 방문객 수: {total_v:,}명"
        )
        if yoy is not None:
            stats_str += f"\n- 전년 동월 대비 증감률: {yoy * 100:.1f}%"
        if female is not None and male is not None:
            stats_str += f"\n- 성비 구성: 여성 {female * 100:.1f}% / 남성 {male * 100:.1f}%"

        if location_resolution:
            # 전역 변수인 _MARKET_METRIC_LABELS 를 직접 사용합니다.
            metric_label = _MARKET_METRIC_LABELS.get(
                location_resolution.get("metric"), location_resolution.get("metric")
            )
            stats_str += (
                f"\n- [지역 자동 선정 근거]: '{location_resolution.get('region_dong')}'은 "
                f"{location_resolution.get('year_month')} 기준 {metric_label} 1위 지역으로 자동 선정됨."
            )

        stats_str += (
            f"\n\n[통계 기반 상품 설계 권장 사항]\n"
            f"- 월간 {total_v:,}명 수준의 통계 수치를 고려하여, 다중 밀집 구역 내 안전 요원 배치 상태 파악 및 비상 대피 통로 확보 가이드 수립 필요."
        )
        if yoy is not None and yoy > 0:
            stats_str += f"\n- 전년 대비 방문객 증가 추세({yoy * 100:.1f}% 증가)를 반영한 주차난 해소 및 셔틀버스 증편 계획 수립 권장."

        answer_parts.append(stats_str)

    # 3. 제주 로컬 문화 및 작물 지식 RAG 조립
    if should_search_culture and culture_chunks:
        culture_str = "[제주 로컬 문화 및 작물 지식 가이드]"
        for i, chunk in enumerate(culture_chunks):
            title = chunk.get("title")
            content = chunk.get("content")
            crop_name = chunk.get("target_crop") or chunk.get("crop_name")
            active_months = chunk.get("active_months")
            
            culture_str += f"\n- {title}: {content}"
            if crop_name and active_months:
                in_season = target_month in active_months
                months_str = ",".join(str(m) for m in sorted(active_months))
                season_status = "제철" if in_season else "제철 아님"
                culture_str += f"\n  (작물: {crop_name} / 제철 시기: {months_str}월 / 현재 예정월 {target_month}월 기준 {season_status} 상태 반영 필요)"
        answer_parts.append(culture_str)

    # 4. 안전 및 에티켓 지침 조립
    if safety_guide_chunks:
        safety_str = (
            f"[안전 탐방 및 준비물 관리 수칙]\n"
            f"{safety_guide_context_str}\n\n"
            f"[운영 준비물 및 비상 대응 지침]\n"
            f"- 사전 준비 단계에서 여행지, 숙박시설, 교통수단의 안전성을 사전에 철저히 조사할 것.\n"
            f"- 경찰서, 소방서, 인근 병원 등과의 비상 연락망을 필히 확보하고 현장 관리용 구급약 및 비상 용품 구비 상태 점검 권장."
        )
        answer_parts.append(safety_str)

    if answer_parts:
        answer = "\n\n".join(answer_parts).strip().replace("~", "～")
    else:
        answer = (
            "죄송합니다. 질문하신 내용과 관련된 제주 문화·작물 지식, 관광 방문객 통계, "
            "또는 올레길 안전 가이드 정보를 찾지 못했습니다. 질문을 조금 더 구체적으로 말씀해 주시면 다시 찾아보겠습니다."
        )

    # 보완/정정한 검색 조건을 state 에 다시 써서 하류 노드(tool_agent_node)가 같은 값을 보게 합니다.
    updated_b2b_params = {
        **b2b_params,
        "key_item_or_crop": key_item_or_crop,
        "preferred_location": preferred_location,
    }

    return {
        "b2b_params": updated_b2b_params,
        "course_meta": course_meta,
        "culture_chunks": culture_chunks,
        "market_insight": market_insight,
        "docent_answer": answer,
        "final_response": answer,
    }


def generate_report_node(state: AgentState) -> Dict[str, Any]:
    """검색된 코스 컨텍스트, 실제 세부 구간(km) 데이터, 제주 밭담문화·작물 생육 지식 DB 근거를 엮어
    B2B 관광 상품 기획서의 [📊 B2B 상품 개요 & 스펙]/[📍 타임라인 표](섹션 1·2)를 LLM 으로 작성한 뒤,
    이어서 비짓제주 API 기반 [☕ 로컬 상생 제휴 아이디어]/[🌤️ 기후 리스크 및 Plan B]/
    [🛡️ Trust Tagging](섹션 3·4·5)를 같은 노드 안에서 순차적으로 완결하는 Report Generator
    노드입니다. course_recommendation 의도(무거운 전체 파이프라인)에서만 실행되므로(그 외 의도는
    quick_responder 로 우회) 별도 조건부 분기 없이 항상 5개 섹션 전체를 작성합니다.
    (2026-07-24 이전에는 docent_generator/report_finalizer(당시 이름 local_recommender) 두
    노드로 나뉘어 있었고, should_finalize_report 라우터가 intent_category==course_recommendation
    일 때만 후자를 실행했는데, 이 조건은 route_after_location_resolve 가 애초에 course_recommendation
    이 아니면 이 경로 자체에 진입시키지 않으므로 항상 참이었습니다 — 그래서 이 노드로 통합.)
    관광 API 데이터는 폐업/변경에 취약해 특정 매장을 "검증된 제휴처"로 단정할 수 없으므로,
    매장명/주소/전화번호는 결과물에 노출하지 않고 지역 상점의 성격(introduction)만 아이디어의
    참고 재료로 사용합니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _QUALITY_COMMENT_PLACEHOLDER,
        _SELF_RAG_STARS_PLACEHOLDER,
        _build_price_breakdown_str,
        _estimate_price_range,
        _resolve_effective_crops,
        get_chat_completion,
        get_supabase_client,
        get_visit_jeju_recommendations,
    )

    # pyrefly: ignore [missing-import]
    from src.services.db_service import _fetch_safety_etiquette_guide

    query = state["query"]
    chunks = state["retrieved_chunks"]
    culture_chunks = state.get("culture_chunks") or []
    sub_segments = state.get("sub_segments") or []
    fallback = state["fallback_applied"]
    reason = state["fallback_reason"]
    weather = state["weather_info"] or {}
    safety = state["safety_check"] or {}
    market_insight = state.get("market_insight")
    b2b_params = state.get("b2b_params") or {}
    target_audience = b2b_params.get("target_audience") or "family"
    include_market_insights = b2b_params.get("include_market_insights", True)
    # 방문 예정월이 질의에 명시되지 않으면 오늘 날짜의 월을 기준으로 제철 여부를 판단합니다
    # (safety_evaluator_node 의 기본값 처리 방식과 동일).
    target_month = b2b_params.get("target_month") or date.today().month

    client = get_supabase_client()
    safety_guide_chunks = _fetch_safety_etiquette_guide(client)

    if not chunks:
        # fallback_reason 이 있으면(예: 지정한 코스가 하드 제약을 만족 못 해 retrieve_rag_node 가
        # 대체 추천 없이 검색을 중단시킨 경우) 그 구체적인 사유를 그대로 안내합니다. 다른 코스로
        # 조용히 대체하지 않고 "왜 기획서를 못 만드는지"만 정직하게 답하고 종료합니다(사용자 요청).
        if fallback and reason:
            fallback_msg = f"요청하신 조건으로는 기획서를 작성할 수 없습니다. {reason}"
        else:
            fallback_msg = "죄송합니다. 요청하신 조건(코스/작물/시기)에 부합하는 제주올레길 코스 데이터를 데이터베이스에서 찾을 수 없었습니다. 입력 조건을 다시 확인해 주세요."
        return {"docent_answer": fallback_msg, "final_response": fallback_msg}

    # 코스 컨텍스트 빌드. 여러 코스가 함께 검색되더라도 섹션 1·2가 실제로 상품화하는 대상은
    # 항상 chunks[0](유사도 순위 최상위 매칭 코스 — price_range_str/course_name 등 다른 계산도
    # 전부 chunks[0] 기준)뿐입니다. 각 코스 블록에 "이 코스의 재배작물/행정구역" 형태로 소속을
    # 문장 단위까지 명시하고 코스 1에는 별도 역할 라벨을 붙여, LLM이 코스 2 이후의 crops를 코스
    # 1 얘기인 것처럼 자유 연상으로 섞어 쓰지 않도록 구조적으로 고정합니다(회귀 방지: 예전엔
    # 코스명과 crops 목록이 근접 배치만 되어 있어 상위 매칭이 아닌 다른 코스의 crops가 상품
    # 설명에 잘못 귀속되는 사례가 실사용 중 확인됨 — 예: 최상위 매칭 15-B코스(crops=마늘)에
    # 14코스의 crops(감귤/양배추)가 서술됨).
    context_str = ""
    for i, c in enumerate(chunks):
        role_label = (
            "★ 최상위 매칭 코스 - 이번 상품 기획서가 실제로 상품화하는 유일한 대상"
            if i == 0
            else "참고용 코스 (상품화 대상이 아님 - 아래 최상위 매칭 코스 서술에 이 코스의 정보를 섞지 말 것)"
        )
        context_str += f"\n[코스 {i+1} - {role_label}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
        context_str += f"※ {c['course_name']}의 재배작물: {c['crops']} / {c['course_name']}의 경유 행정구역: {c['administrative_areas']}\n"
        context_str += f"내용: {c['content']}\n"

    # 밭담문화·작물 생육 지식 DB 컨텍스트 빌드 (외부 API 대신 문서 근거 확보)
    culture_context_str = _build_culture_context_str(culture_chunks, target_month)
    if not culture_context_str:
        # 근거 문서가 없다고 지어낸 일반 지식으로 채우지 말고, 근거가 없다는 사실 자체를 있는
        # 그대로 반영하도록 지시합니다(사용자 요청: 관련 정보가 없으면 솔직하게 답할 것).
        culture_context_str = "(관련 문화/작물 지식 문서를 찾지 못했습니다. 지어내지 말고, 이 지점은 문화/작물 근거 없이 코스 사실 정보 위주로만 서술하세요.)"

    # 실제 세부 구간(구간명 + 누적 km) 컨텍스트 빌드 - 타임라인 표의 유일한 사실 근거
    if sub_segments:
        segments_str = "\n".join(f"- {s['sub_segment_name']} ({s['distance_km']}km)" for s in sub_segments)
    else:
        segments_str = "(세부 구간 데이터 없음 - 타임라인 표는 Start/Finish 위주로 간략히 구성)"

    # 제주관광공사 방문객 빅데이터(Market Insight) 컨텍스트 빌드 - 섹션 1 하단에 필수 기재
    # 타겟 고객층에 따라 강조할 지표를 코드에서 결정해 지시문으로 넘김 (LLM이 임의로 고르지 않도록).
    # 단, 그 행정동/월에 원본 순위표 데이터가 없어 실제로는 None인 지표를 "두드러진다"는 식으로
    # 단정하지 않도록, 강조 후보는 market_insight 에 실제로 값이 있는 지표로만 제한합니다.
    _AUDIENCE_RATIO_PRIORITY = {
        "family": ["youth_10s_ratio", "middle_4060_ratio"],
        "corporate": ["middle_4060_ratio"],
        "healing": ["young_2030_ratio", "middle_4060_ratio"],
        "senior": ["senior_70s_ratio", "middle_4060_ratio"],
        "active": ["young_2030_ratio"],
    }
    _RATIO_FIELD_LABELS = {
        "youth_10s_ratio": "10대 이하 비중",
        "young_2030_ratio": "2030대 비중",
        "middle_4060_ratio": "40~60대 비중",
        "senior_70s_ratio": "70대 이상 비중",
    }
    if not include_market_insights or not market_insight:
        market_insight_context_str = "(빅데이터 지표 없음 - 정성적 제안만 작성하고 수치는 지어내지 마세요)"
        emphasis_instruction = ""
    else:
        parts = [f"{market_insight['region_dong']} {market_insight['year_month']} 방문객 {market_insight['total_visitors']:,}명"]
        if market_insight.get("yoy_growth_rate") is not None:
            parts.append(f"(전년 대비 {market_insight['yoy_growth_rate']}%)")
        if market_insight.get("female_ratio") is not None:
            parts.append(f"여성 비중 {market_insight['female_ratio']}%")
        if market_insight.get("young_2030_ratio") is not None:
            parts.append(f"2030 청년층 비중 {market_insight['young_2030_ratio']}%")
        if market_insight.get("middle_4060_ratio") is not None:
            parts.append(f"4060대 비중 {market_insight['middle_4060_ratio']}%")
        if market_insight.get("senior_70s_ratio") is not None:
            parts.append(f"70대 이상 비중 {market_insight['senior_70s_ratio']}%")
        if market_insight.get("foreign_visitors") is not None:
            parts.append(f"외국인 방문객 {market_insight['foreign_visitors']:,}명")
        market_insight_context_str = "📊 " + ", ".join(parts)

        # 강조 후보 지표 중 실제로 값이 있는 것만 선택 (없는 지표를 "두드러진다"고 단정 금지)
        priority_fields = _AUDIENCE_RATIO_PRIORITY.get(target_audience, _AUDIENCE_RATIO_PRIORITY["family"])
        available_labels = [
            _RATIO_FIELD_LABELS[f] for f in priority_fields if market_insight.get(f) is not None
        ]
        if available_labels:
            emphasis_instruction = "과 ".join(available_labels) + " (이 값들만 실제 데이터이니 이 항목만 언급하세요)"
        else:
            emphasis_instruction = (
                "해당 타겟층 연령대 비율 데이터 없음 - 방문객 수/증감률/외국인 수만 언급하고 "
                "연령대 비중은 절대 언급하지 마세요"
            )

    # 지역명이 아니라 방문객 통계 조건(예: "외국인이 많았던 지역")으로 질문했을 때, 그 조건으로
    # 어떤 지역이 왜 선정됐는지를 LLM 이 상품 개요에 근거로 밝히도록 컨텍스트에 명시합니다.
    market_location_resolution = b2b_params.get("market_location_resolution")
    if market_location_resolution:
        metric_label = _MARKET_METRIC_LABELS.get(
            market_location_resolution["metric"], market_location_resolution["metric"]
        )
        location_resolution_str = (
            f"이 상품의 대상 지역은 사용자가 지역명을 직접 지정하지 않고 \"{metric_label}\" 기준으로 요청하여, "
            f"{market_location_resolution['year_month']} 기준 {metric_label} "
            f"{'1위' if market_location_resolution['direction'] == 'desc' else '최하위'} 지역인 "
            f"{market_location_resolution['region_dong']}(값: {market_location_resolution['value']})으로 "
            f"visitor_analytics 데이터 조회를 통해 자동 선정되었습니다."
        )
    else:
        location_resolution_str = "(해당 없음 - 사용자가 지역을 직접 지정했거나 지역 조건이 없는 질의)"

    # 예상 1인 단가 범위 산정에 필요한 작물×행정구역 조합 수를 미리 세어둡니다(섹션 3의 비짓제주
    # API 조회용 unique_combos 와 동일한 조합 집합이지만, 가격 산정은 조합 "개수"만 필요하고
    # API 응답 내용은 필요 없으므로 여기서는 API 호출 없이 개수만 계산합니다 — 실제 조합 리스트/
    # API 조회는 섹션 3에서 그대로 재사용합니다). 섹션 3이 1순위 코스(chunks[0])로만 제한되므로
    # 여기서도 반드시 chunks[0]만 세야 합니다 — 그렇지 않으면 검색된 다른 코스의 조합까지 단가에
    # 반영되어, 실제로 섹션 3에 나오지 않는 조합 수를 근거로 가격이 산정되는 불일치가 생깁니다.
    # strict_single_crop(예: "당근만"/"오직 마늘만") 이면 이 코스의 다른 공동 재배 작물은
    # 단가 산정 조합 수/로컬 제휴 조회/아이디어 프롬프트에서 모두 동일하게 제외합니다 —
    # 이 노드 뒷부분(unique_combos, 로컬 제휴 아이디어 프롬프트)에서도 재사용하는 값이라
    # chunks[0]/b2b_params 가 바뀌지 않는 이 함수 안에서는 한 번만 계산합니다.
    _effective_top_course_crops = _resolve_effective_crops(chunks[0], b2b_params)
    _price_combo_set = set()
    for crop in _effective_top_course_crops:
        for area in [x.strip() for x in chunks[0]["administrative_areas"].split(",") if x.strip()]:
            _price_combo_set.add((crop, area))
    price_range_str = _estimate_price_range(chunks[0], target_audience, len(_price_combo_set))
    price_breakdown_str = _build_price_breakdown_str(chunks[0], target_audience, len(_price_combo_set))

    # strict_single_crop 이 실제로 적용된 경우(타겟 작물이 코스 1의 실제 crops 목록에 있어
    # _resolve_effective_crops 가 단일 작물로 좁혔을 때)에만, 섹션 1·2 작성 LLM에게 다른 작물을
    # 일절 언급하지 말라는 절대 규칙을 추가로 지시합니다. 대상 작물이 이 코스에서 재배되지
    # 않아 가드가 적용되지 않은 경우(fail-soft)는 이 지시도 함께 생략합니다 — 지시만 내리고
    # 실제 데이터 필터링은 하지 않으면 "왜 코스 1의 crops 에 있는 다른 작물을 숨기라는 거냐"는
    # 모순이 생기므로, 이 지시는 항상 _resolve_effective_crops 의 실제 판단과 함께 움직여야 합니다.
    _strict_single_crop_target = (b2b_params.get("key_item_or_crop") or "").strip()
    strict_single_crop_applied = (
        bool(b2b_params.get("strict_single_crop"))
        and bool(_strict_single_crop_target)
        and _effective_top_course_crops == [_strict_single_crop_target]
    )
    if strict_single_crop_applied:
        strict_single_crop_rule_str = (
            f"5. 사용자가 \"{_strict_single_crop_target}만\"/\"오직 {_strict_single_crop_target}만\" 처럼 "
            f"이번 상품을 \"{_strict_single_crop_target}\" 단일 작물로만 배타적으로 한정해 달라고 "
            f"요청했습니다. [코스 1]의 실제 재배작물 목록에 다른 작물이 더 있더라도, 섹션 1(상품명/"
            f"USP/Market Insight 서술)과 섹션 2(타임라인 해설)에서 \"{_strict_single_crop_target}\" 이외의 "
            f"작물은 이름조차 언급하거나 암시하지 마세요 — 상품명/USP/타임라인 포인트 모두 "
            f"\"{_strict_single_crop_target}\" 단일 테마로만 서술하세요."
        )
    else:
        strict_single_crop_rule_str = ""

    # 조건 완화(fallback) 각주 지시문을 Python 에서 미리 완결된 문장으로 만들어 프롬프트에
    # 넣습니다. 예전엔 "적용 여부는 {fallback} 입니다" 처럼 True/False 리터럴을 그대로
    # 프롬프트에 박아 넣었는데, LLM이 이 사실-서술문 형태를 실제 리포트에 인용해도 되는
    # 내용으로 착각해 "조건 완화 적용 여부: False" 같은 디버그성 문구를 리포트에 그대로
    # 출력하는 사고가 있었습니다(회귀 방지). fallback_applied 는 2026-07-25 fail-fast
    # 정책 이후 항상 False 라 사실상 else 분기만 타지만, 상태값 자체는 그대로 두고 프롬프트에
    # 노출되는 표현만 "지시문" 형태로 바꿉니다.
    if fallback and reason:
        fallback_note_rule_str = (
            f"- 표 바로 다음 줄에 이 각주를 정확히 한 줄만 추가하세요: \"완화 사유: {reason}\""
        )
    else:
        fallback_note_rule_str = "- 조건 완화 각주는 추가하지 마세요. 표로 섹션을 바로 종료하세요."

    # 휠체어 요구사항 발생 시, 동적 휠체어 전용 구간 매핑 지침 정의
    # (state["parsed_constraints"] 는 필수 키이지만 값 자체가 None 일 수 있는 Optional 필드라,
    # dict.get(key, {}) 의 기본값은 "키가 없을 때"만 적용되고 "값이 None"인 경우는 구제하지
    # 못합니다 - 이 파일의 다른 곳(quick_responder_node)과 동일하게 `or {}` 로 널 안전하게 처리합니다.)
    hard = (state.get("parsed_constraints") or {}).get("hard_constraints", {})
    if hard.get("wheelchair_required"):
        wheelchair_guideline_str = (
            "※ 중요(휠체어 전용 구간): 본 상품은 휠체어 이용자를 위한 특수 기획 상품입니다. "
            "타임라인(## 2. 📍) 및 주요 도슨트 해설을 구성할 때 비포장 흙길, 오름 등 휠체어 진입이 불가능한 위험 구간을 전면 배제하십시오. "
            "반드시 포장(우레탄/아스팔트/보도블록)이 확보된 휠체어 전용 보행 구간(종달리 옛 소금밭 ~ 성산갑문 입구)으로 동선을 국한하고, "
            "흙길 진입을 권장하는 유인 문구를 일절 작성하지 마십시오."
        )
    else:
        wheelchair_guideline_str = ""

    system_prompt = load_prompt("generate_report.md").format(
        weather_description=weather.get('description', ''),
        weather_warnings=', '.join(weather.get('warnings', [])) or '없음',
        wheelchair_guideline_str=wheelchair_guideline_str,
        culture_context_str=culture_context_str,
        segments_str=segments_str,
        market_insight_context_str=market_insight_context_str,
        target_audience=target_audience,
        emphasis_instruction=emphasis_instruction or "(없음 - 정성적 제안만)",
        location_resolution_str=location_resolution_str,
        price_range_str=price_range_str,
        price_breakdown_str=price_breakdown_str,
        fallback_note_rule_str=fallback_note_rule_str,
        strict_single_crop_rule_str=strict_single_crop_rule_str,
    )

    user_msg = f"[질문(방문 조건)]: {query}\n\n[검색 결과 컨텍스트]:\n{context_str}"

    docent_answer = get_chat_completion(system_prompt, user_msg)

    # --- 섹션 3·4·5 (로컬 상생 제휴 아이디어 / 기후 리스크 & Plan B / Trust Tagging) ---
    # 섹션 1·2 작성이 비정상적으로 빈 문자열을 반환한 경우를 대비한 안전망(사실상 발생하지 않음 —
    # chunks 는 위에서 이미 비어있지 않음을 확인함). 이 경로에서는 섹션 3~5 없이 그대로 반환합니다.
    if not docent_answer:
        return {"docent_answer": docent_answer, "final_response": docent_answer, "recommendations": []}

    recommendations = []
    introduction_snippets = []
    rec_cache: Dict[Any, Any] = {}

    # 검색된 상위 코스들의 작물 및 행정구역 조합에 대해 비짓제주 소개 정보를 참고 재료로 수집합니다.
    # 조합 개수만큼 API 호출이 하나씩 순서대로 쌓이면 지연이 누적되므로(조합이 여러 개인 리포트일수록
    # 체감 지연이 커짐), 먼저 중복 없는 조합만 추려 스레드풀로 동시에 조회한 뒤(get_visit_jeju_recommendations
    # 는 내부적으로 실패 시 예외를 던지지 않고 Mock 데이터로 폴백하므로 여기서의 except 는 순수 방어용),
    # 그 결과를 원래 코스 순서대로 다시 조립합니다 — 조립 단계는 API 호출이 없는 순수 로컬 연산이라
    # 병렬화할 필요가 없습니다.
    unique_combos = []
    seen_combos = set()
    if chunks:
        chunk = chunks[0]
        crops = _effective_top_course_crops
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]
        for crop in crops:
            for area in areas:
                combo = (crop, area)
                if combo not in seen_combos:
                    seen_combos.add(combo)
                    unique_combos.append(combo)

    if unique_combos:
        with ThreadPoolExecutor(max_workers=min(8, len(unique_combos))) as executor:
            future_to_combo = {
                executor.submit(get_visit_jeju_recommendations, crop, area): (crop, area)
                for crop, area in unique_combos
            }
            for future, combo in future_to_combo.items():
                try:
                    rec_cache[combo] = future.result()
                except Exception as e:
                    print(f"[!] 비짓제주 API 조회 실패(작물={combo[0]}, 지역={combo[1]}), 이 조합은 건너뜁니다: {e}")
                    rec_cache[combo] = []

    if chunks:
        chunk = chunks[0]
        crops = _effective_top_course_crops
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]

        for crop in crops:
            for area in areas:
                rec_list = rec_cache.get((crop, area), [])
                for rec in rec_list:
                    recommendations.append(rec)
                    intro = (rec.get("introduction") or "").strip()
                    if intro and intro not in introduction_snippets:
                        introduction_snippets.append(intro)

    # ## 3. ☕ 로컬 상생 제휴 및 상품화 아이디어 (표)
    if introduction_snippets:
        reference_str = "\n".join(f"- {s}" for s in introduction_snippets[:6])
        idea_system_prompt = load_prompt("local_ideas.md")
        # strict_single_crop 적용 시(_effective_top_course_crops 가 타겟 작물 1종으로 좁혀진
        # 경우) 아이디어 생성 프롬프트에도 원본 crops 전체 문자열이 아니라 좁혀진 작물만 전달해,
        # 다른 공동 재배 작물이 아이디어에 섞여 들어가지 않게 합니다.
        idea_user_msg = f"[코스 매개 작물/테마]: {', '.join(_effective_top_course_crops)}\n\n[지역 로컬 상점 소개 참고자료]:\n{reference_str}"
        local_ideas = get_chat_completion(idea_system_prompt, idea_user_msg)
    else:
        local_ideas = "*현재 이 지역에 참고할 로컬 상점 소개 정보가 없어 아이디어 제안을 생략합니다.*"

    report = docent_answer.rstrip() + "\n\n"
    report += "## 3. ☕ 로컬 상생 제휴 및 상품화 아이디어\n"
    report += "*(실제 매장 디렉토리가 아니라, 해당 지역 로컬 상점 성격에서 착안한 협업 컨셉 제안입니다. 개별 매장 운영 현황은 별도 확인이 필요합니다.)*\n\n"
    report += local_ideas.rstrip() + "\n"

    # ## 4. 🌤️ 기후 리스크 및 Plan B 우회 동선
    total_distance = chunks[0].get("total_distance_km")
    course_name = chunks[0].get("course_name", "코스")
    report += "\n## 4. 🌤️ 기후 리스크 및 Plan B 우회 동선\n"
    report += f"- **[기후 환경]**: {weather.get('description', '')}"
    warnings = weather.get("warnings") or []
    if warnings:
        report += f" / 유의사항: {', '.join(warnings)}"
    report += "\n"
    report += f"- **[Plan A (정상 운용)]**: {course_name} 전체 코스 풀 도보 트레킹"
    if total_distance:
        report += f" ({total_distance}km)"
    report += "\n"
    if safety.get("reroute_required"):
        plan_b = (safety.get("alternative_query_override") or "해안 구간 대신 중산간/숲길 우회 동선").strip()
        status = safety.get("safety_status", "WARNING")
        if plan_b.endswith((".", "!", "?")):
            # weather_info.guideline 처럼 이미 완결된 권고 문장이 들어온 경우입니다. 명사구를
            # 가정한 "{plan_b}으로 전환" 을 그대로 이어붙이면 "…권장하세요.으로 전환" 처럼
            # 문장이 꼬이므로(회귀 방지), 별도 문장으로 분리해 자연스럽게 이어 씁니다.
            report += (
                f"- **[Plan B (우회/대체)]**: {status} 상황 시 {plan_b} "
                f"필요 시 실내 체험 프로그램으로 대체하세요.\n"
            )
        else:
            report += (
                f"- **[Plan B (우회/대체)]**: {status} 상황 시 {plan_b}{_josa_ro(plan_b)} 전환, "
                f"필요 시 실내 체험 프로그램으로 대체\n"
            )
    else:
        report += "- **[Plan B (우회/대체)]**: 현재 특이 리스크는 없으나, 돌발 강풍·우천 시를 대비해 단축 동선 및 실내 체험/휴게 프로그램으로의 전환 대안을 상시 준비\n"

    # ## 5. 🎒 로컬 안전 탐방 가이드 및 준비물
    safety_guide_system_prompt = load_prompt("safety_guide.md")
    safety_guide_context = _build_safety_guide_context_str(safety_guide_chunks)
    if safety_guide_context:
        weather_description = weather.get("description", "정보 없음")
        safety_status = safety.get("safety_status", "SAFE")
        weather_guideline = weather.get("guideline", "특별한 기상 리스크가 없습니다.")
        
        weather_context_str = (
            f"- 방문 예정 월: {target_month}월\n"
            f"- 기상 상태: {weather_description} (리스크 단계: {safety_status})\n"
            f"- 기상 및 동선 가이드라인: {weather_guideline}"
        )
        
        user_message = (
            f"[안전 및 에티켓 가이드 데이터]:\n{safety_guide_context}\n\n"
            f"[현재 탐방 계절 및 기상 상황 정보]:\n{weather_context_str}"
        )
        
        safety_guide_ideas = get_chat_completion(
            safety_guide_system_prompt,
            user_message
        )
    else:
        safety_guide_ideas = "*현재 안전 탐방 가이드 및 준비물 정보가 확인되지 않아 생략합니다.*"

    report += "\n## 5. 🎒 로컬 안전 탐방 가이드 및 준비물\n"
    report += safety_guide_ideas.strip() + "\n"

    # ## 6. 🛡️ Trust Tagging — 고정 문구가 아니라 이번 리포트에 실제로 쓰인 데이터 출처를
    # 구체적으로 나열합니다(예: "2026년 5월 제주관광공사 이동통신 빅데이터 기반 OO동 방문객
    # 통계"). 로컬 제휴 아이디어는 실 비짓제주 API 응답인지 Mock 폴백인지도 구분해서 밝힙니다 —
    # 방화벽/응답 지연으로 실 API가 막혀 있을 때 실 데이터인 것처럼 표시하면 안 되므로.
    source_labels = []

    course_names = []
    for c in chunks:
        name = c.get("course_name")
        if name and name not in course_names:
            course_names.append(name)
    if course_names:
        source_labels.append(f"제주올레 {'·'.join(course_names)} 원문 가이드북")

    if culture_chunks:
        crop_names = []
        for cc in culture_chunks:
            crop = cc.get("target_crop") or cc.get("crop_name")
            if crop and crop not in crop_names:
                crop_names.append(crop)
        if crop_names:
            source_labels.append(f"제주 밭담문화·작물 지식 DB({'·'.join(crop_names)} 등 {len(culture_chunks)}건)")
        else:
            source_labels.append(f"제주 밭담문화·작물 지식 DB({len(culture_chunks)}건)")

    if safety_guide_chunks:
        source_labels.append("제주 안전·에티켓 가이드 DB")

    if market_insight:
        year, month = market_insight["year_month"].split("-")
        source_labels.append(
            f"{year}년 {int(month)}월 제주관광공사 이동통신 빅데이터 기반 "
            f"{market_insight['region_dong']} 방문객 통계"
        )

    rec_sources = {rec.get("source") for rec in recommendations if rec.get("source")}
    if "visitjeju_api" in rec_sources:
        source_labels.append("비짓제주 실 API 기반 제휴 아이디어")
    elif "mock_db" in rec_sources:
        source_labels.append("비짓제주 Mock 데이터 기반 제휴 아이디어(실 API 미가동 시 대체)")

    # 별점은 이 시점에서 확정할 수 없습니다 — 그래프 순서상 실제 Self-RAG 단계인 quality_checker 가
    # 아직 실행되기 전이라 quality_report 가 없기 때문입니다. 그래서 이전에는 여기서
    # fallback_applied 여부만으로 4/5점을 임의로 매겼는데, "Self-RAG 신뢰도"라는 라벨과 실제
    # 근거가 안 맞는 문제였습니다. 자리표시자만 남겨두고, check_quality_node 가 자신의 실제
    # 평가 결과로 치환합니다.
    source_labels.append(f"Self-RAG 신뢰도: {_SELF_RAG_STARS_PLACEHOLDER}")
    # 별점과 마찬가지로 이 시점에는 확정할 수 없어 자리표시자만 남겨두고, check_quality_node가
    # 자신의 평가 결과(passed/feedback)로 만든 한 줄 평으로 치환합니다.
    source_labels.append(f"품질 평가: {_QUALITY_COMMENT_PLACEHOLDER}")

    report += "\n## 6. 🛡️ Trust Tagging\n"
    report += f"[출처: {' / '.join(source_labels)}]\n"

    docent_answer = docent_answer.replace("~", "～")
    report = report.replace("~", "～")
    return {
        "docent_answer": docent_answer,
        "recommendations": recommendations,
        "final_response": report
    }
