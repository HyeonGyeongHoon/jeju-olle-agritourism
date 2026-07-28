"""의도 분류 / 조건 파싱 / 통계 기반 지역 해석 노드.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

import json
from datetime import date
from typing import Any, Dict

from src.agent.prompts.loader import load_prompt
from src.agent.state import AgentState
from src.models.schema import B2BQueryParams, IntentCategory

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


def classify_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자 질의를 3가지 카테고리로 사전 분류하는 Intent Classifier 노드입니다.
    (분류 결과로 state의 intent_category 에 라벨을 붙이며, 실제 물리적 분기는
    graph.py의 route_after_location_resolve 에서 수행합니다.)
    호출부(B2B 구조화 입력 등)가 intent_category 를 이미 확정해 넘긴 경우, LLM 분류 호출 없이
    그대로 통과시키되, IntentCategory enum 에 없는 값(오타/구버전 카테고리명 등)이면 신뢰하지 않고
    route_intent 로 새로 분류합니다 — 잘못된 문자열이 하류의 문자열 비교 분기들을 예측 불가능하게
    만드는 것을 막기 위함입니다.
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
    핵심 파라미터(방문 시기, 매개 작물/테마, 선호 지역, 컨셉)를 추출하여 정형화하는
    Intent Parser 노드입니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        get_chat_completion,
    )

    query = state["query"]

    system_prompt = load_prompt("parse_intent.md")

    try:
        raw_res = get_chat_completion(system_prompt, query)

        # 코드 펜스 제거
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
        # 하드 제약조건(휠체어 등)은 완화되면 안 되는 불변 조건이므로, LLM 파싱 자체가 실패했다고
        # 조용히 False 로 초기화하지 않고 원본 질의에서 최소한의 키워드 재확인을 거칩니다.
        # 이 재확인은 "true 로 완화"만 방지하기 위한 안전망이지, 완벽한 대체 파서는 아닙니다.
        fallback_wheelchair_required = "휠체어" in query
        parsed = {
            "hard_constraints": {"wheelchair_required": fallback_wheelchair_required},
            "vector_query": query
        }
        b2b_params = B2BQueryParams().model_dump()

    return {"parsed_constraints": parsed, "b2b_params": b2b_params}


def resolve_market_location_node(state: AgentState) -> Dict[str, Any]:
    """"외국인 관광객이 많았던 지역에서 상품을 기획하고 싶어"처럼, 지역명이 아니라 방문객 통계
    조건으로 지역을 지목한 질의를 처리하는 노드입니다. parse_intent_node 가 LLM 으로 뽑아낸
    구조화 파라미터(market_location_query: metric/year/month/direction)를 받아, 그 조건 그대로
    visitor_analytics 테이블을 조회(select+eq+order+limit)해 1위 지역을 찾고, 이후 retriever/
    report_generator 가 그대로 소비하도록 b2b_params.preferred_location 에 채워 넣습니다.
    LLM 이 직접 SQL 문자열을 생성해 실행하지 않고 metric 을 Enum 화이트리스트로 제한한 뒤 Supabase
    쿼리 빌더로만 조회하는 방식이라, SQL 인젝션 경로 자체가 없습니다.
    market_location_query 가 없거나(metric=null) 이미 preferred_location 이 직접 언급된 질의라면
    아무 것도 하지 않고 그대로 통과시킵니다.
    올레 코스가 지나지 않는 행정동(예: 제주시 도심 연동·노형동 등)이 통계상 1위여도 코스 추천과
    무관한 지역이 뽑히는 것을 막기 위해, courses.administrative_areas 기반으로 실제 코스가 있는
    행정동/읍/면으로 후보를 좁혀서 조회합니다(_get_olle_relevant_admin_dongs). 이 후보 목록을
    확인할 수 없으면(조회 실패든, courses 테이블에 아직 데이터가 없어 정말 후보가 없는 것이든)
    "코스와 무관한 지역이 통계 1위라는 이유만으로 선정되는 것"을 막기 위해 통계 기반 지역 자동
    선정 자체를 fail-closed 로 건너뜁니다 — 후보를 모르면 무제한 검색으로 느슨하게 폴백하지
    않습니다(과거에는 그렇게 했었는데, 이는 위 도메인 규칙을 그대로 어길 수 있는 경로였음).
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _MARKET_METRIC_LABELS,
        _get_latest_available_year_month,
        _get_olle_relevant_admin_dongs,
        get_supabase_client,
    )

    b2b_params = state.get("b2b_params") or {}
    query_spec = b2b_params.get("market_location_query")
    if not query_spec or not query_spec.get("metric") or b2b_params.get("preferred_location"):
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
            print(
                "[!] 올레 코스가 지나는 행정동을 확인할 수 없어(조회 실패 또는 데이터 없음), "
                "코스와 무관한 지역이 선정되는 것을 막기 위해 통계 기반 지역 자동 선정을 건너뜁니다."
            )
            return {}

        if year and month:
            year_month = f"{year}-{month:02d}"
        elif month:
            # 월만 지정되고 연도가 없으면 올해로 간주 (기존 동작 유지)
            year_month = f"{date.today().year}-{month:02d}"
        else:
            # 연/월을 전혀 지정하지 않은 경우에만 "오늘 날짜" 대신 실제 데이터가 있는 가장
            # 최근 달을 기본값으로 사용 — 사용자가 명시한 월은 데이터가 없어도 그대로 존중합니다.
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
        print(f"[!] 방문객 통계 기반 지역 검색(visitor_analytics) 실패: {e}")
        return {}

    if not res.data:
        print(f"[!] {year_month} 기준 {metric} 데이터가 없어(올레 코스 지역 범위 내) 통계 기반 지역 자동 선정을 건너뜁니다.")
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
