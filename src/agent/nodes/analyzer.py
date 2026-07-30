"""통합 의도 분석 및 지역 해석 노드.

(2026-07-29 nodes.py 분할 및 구조 간소화 리팩토링)
"""

import json
from datetime import date
from typing import Any, Dict

# pyrefly: ignore [missing-import]
from src.agent.prompts.loader import load_prompt
# pyrefly: ignore [missing-import]
from src.agent.state import AgentState
# pyrefly: ignore [missing-import]
from src.models.schema import B2BQueryParams, IntentCategory


def classify_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자 질의를 3가지 카테고리로 사전 분류하는 Intent Classifier 노드입니다.
    호출부(B2B 구조화 입력 등)가 intent_category 를 이미 확정해 넘긴 경우, LLM 분류 호출 없이
    그대로 통과시키되, IntentCategory enum 에 없는 값(오타/구버전 카테고리명 등)이면 신뢰하지 않고
    route_intent 로 새로 분류합니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        route_intent,
    )

    preset_category = state.get("intent_category")
    if preset_category and preset_category in {c.value for c in IntentCategory}:
        return {
            "intent_category": preset_category,
            "target_course": state.get("target_course"),
            "tool_calls": None,
            "tool_outputs": None,
            "quality_report": None,
            "tool_depth": 0,
        }

    result = route_intent(state["query"])
    return {
        "intent_category": result.category.value,
        "target_course": result.target_course,
        "tool_calls": None,
        "tool_outputs": None,
        "quality_report": None,
        "tool_depth": 0,
    }


def parse_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자의 자연어 질문에서 의도, Hard 제약 조건, 그리고 B2B 기획서 생성에 필요한
    핵심 파라미터를 추출하여 정형화하는 Intent Parser 노드입니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        get_chat_completion,
    )

    query = state["query"]
    system_prompt = load_prompt("parse_intent.md")

    try:
        raw_res = get_chat_completion(system_prompt, query)

        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        b2b_params = B2BQueryParams(
            target_month=parsed.get("target_month"),
            season=parsed.get("season"),
            key_item_or_crop=parsed.get("key_item_or_crop"),
            preferred_location=parsed.get("preferred_location"),
            market_location_query=parsed.get("market_location_query"),
            concept_theme=parsed.get("concept_theme"),
            target_audience=parsed.get("target_audience") or "family",
            include_market_insights=parsed.get("include_market_insights", True),
            strict_single_crop=parsed.get("strict_single_crop", False),
        ).model_dump(mode="json")
    except Exception as e:
        print(f"[!] 의도 파싱 중 오류 발생: {e}. 기본 제약 조건으로 폴백합니다.")
        fallback_wheelchair_required = "휠체어" in query
        parsed = {
            "hard_constraints": {"wheelchair_required": fallback_wheelchair_required},
            "vector_query": query
        }
        b2b_params = B2BQueryParams().model_dump()

    return {"parsed_constraints": parsed, "b2b_params": b2b_params}


def resolve_market_location_node(state: AgentState) -> Dict[str, Any]:
    """"통계 조건으로 지역을 지목한 질의를 처리하여 preferred_location에 채워 넣는 노드입니다."""
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _MARKET_METRIC_LABELS,
        _get_latest_available_year_month,
        _get_olle_relevant_admin_dongs,
        get_supabase_client,
        _search_culture_knowledge,
    )

    b2b_params = state.get("b2b_params") or {}
    query_spec = b2b_params.get("market_location_query")
    preferred_loc = b2b_params.get("preferred_location")
    key_crop = b2b_params.get("key_item_or_crop")

    # preferred_location이 없고 key_crop이 지정된 경우, 해당 작물의 주산지를 먼저 매핑합니다.
    if not preferred_loc and key_crop:
        try:
            client = get_supabase_client()
            chunks = _search_culture_knowledge(client, key_crop, key_crop)
            for chunk in chunks:
                reg_tag = chunk.get("region_tag")
                if reg_tag:
                    preferred_loc = reg_tag
                    print(f"[*] 작물('{key_crop}') 기반 주산지 선정: {preferred_loc}")
                    break
        except Exception as e:
            print(f"[!] 작물 기반 주산지 검색 중 오류 발생: {e}")

    if not query_spec or not query_spec.get("metric") or preferred_loc:
        if preferred_loc and preferred_loc != b2b_params.get("preferred_location"):
            updated_b2b_params = dict(b2b_params)
            updated_b2b_params["preferred_location"] = preferred_loc
            return {"b2b_params": updated_b2b_params}
        return {}

    metric = query_spec["metric"]
    if metric not in _MARKET_METRIC_LABELS:
        return {}

    year = query_spec.get("year")
    month = query_spec.get("month") or b2b_params.get("target_month")
    direction = query_spec.get("direction") or "desc"

    try:
        client = get_supabase_client()
        olle_dongs = _get_olle_relevant_admin_dongs(client)
        if not olle_dongs:
            print("[!] 올레 코스가 지나는 행정동을 확인할 수 없어 자동 선정을 건너뜁니다.")
            return {}

        if year and month:
            year_month = f"{year}-{month:02d}"
        elif month:
            year_month = f"{date.today().year}-{month:02d}"
        else:
            year_month = _get_latest_available_year_month(client) or (
                f"{date.today().year}-{date.today().month:02d}"
            )

        res = (
            client.table("visitor_analytics")
            .select(f"region_dong,{metric}")
            .eq("year_month", year_month)
            .not_.is_(metric, "null")
            .in_("region_dong", sorted(olle_dongs))
            .order(metric, desc=(direction != "asc"))
            .limit(1)
            .execute()
        )
    except Exception as e:
        print(f"[!] 방문객 통계 기반 지역 검색 실패: {e}")
        return {}

    if not res.data:
        print(f"[!] {year_month} 기준 {metric} 데이터가 없어 자동 선정을 건너뜁니다.")
        return {}

    resolved_region = res.data[0]["region_dong"]
    print(f"[*] 방문객 통계 기반 지역 자동 선정: {resolved_region} ({year_month} {metric}={res.data[0][metric]})")

    updated_b2b_params = dict(b2b_params)
    updated_b2b_params["preferred_location"] = resolved_region
    updated_b2b_params["market_location_resolution"] = {
        "region_dong": resolved_region,
        "metric": metric,
        "value": res.data[0][metric],
        "year_month": year_month,
        "direction": direction,
    }
    return {"b2b_params": updated_b2b_params}


def analyze_intent_node(state: AgentState) -> Dict[str, Any]:
    """의도 분류, 제약 사항 파싱, 통계 기반 지역 선정을 일괄 수행하여 상태를 단순화하는 통합 노드입니다."""
    # 1. 의도 분류
    classify_res = classify_intent_node(state)
    state = {**state, **classify_res}

    # 2. 제약 사항 파싱
    parse_res = parse_intent_node(state)
    state = {**state, **parse_res}

    # 3. 시장 통계 지역 해석
    resolve_res = resolve_market_location_node(state)
    state = {**state, **resolve_res}

    return {
        "intent_category": state.get("intent_category"),
        "target_course": state.get("target_course"),
        "tool_calls": state.get("tool_calls"),
        "tool_outputs": state.get("tool_outputs"),
        "quality_report": state.get("quality_report"),
        "tool_depth": state.get("tool_depth", 0),
        "parsed_constraints": state.get("parsed_constraints"),
        "b2b_params": state.get("b2b_params"),
    }
