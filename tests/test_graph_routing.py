from src.agent.graph import (
    route_after_location_resolve,
    route_after_retriever,
    route_after_rewrite,
)
from src.models.schema import IntentCategory


def test_route_after_location_resolve_sends_only_recommendation_to_full_pipeline():
    """course_recommendation 의도만 full_pipeline 선로로 라우팅되어야 합니다."""
    state = {"intent_category": IntentCategory.COURSE_RECOMMENDATION.value}
    assert route_after_location_resolve(state) == "full_pipeline"


def test_route_after_location_resolve_sends_non_recommendation_to_info_lookup():
    """course_info / olle_general_info / other / info_lookup 은 모두 quick_responder 선로로 라우팅되어야 합니다."""
    for category in (
        IntentCategory.COURSE_INFO,
        IntentCategory.OLLE_GENERAL_INFO,
        IntentCategory.OTHER,
        IntentCategory.INFO_LOOKUP,
    ):
        state = {"intent_category": category.value}
        assert route_after_location_resolve(state) == "quick_response", (
            f"{category.value} 의도는 quick_responder 선로로 라우팅되어야 합니다."
        )


def test_route_after_retriever_sends_exit_early_to_quick_responder():
    """정책 전환(2026-07-25 무조건 반려): retriever 가 DB 매칭 0건으로 검색을 중단하면
    (is_exit_early=True) report_generator 를 호출하지 않고 quick_responder 로 넘겨 반려 사유만
    반환해야 합니다."""
    state = {"is_exit_early": True, "exit_reason": "지역과 겹치는 코스 없음"}
    assert route_after_retriever(state) == "exit_early"


def test_route_after_retriever_sends_normal_result_to_report_generator():
    """대조군: 코스를 찾은 정상 경로는 기존과 동일하게 report_generator 로 진행해야 합니다."""
    assert route_after_retriever({"is_exit_early": False}) == "generate_report"
    # 플래그가 아예 없는 상태(구버전 호출부/테스트 입력)도 반려로 오인하지 않아야 합니다.
    assert route_after_retriever({}) == "generate_report"


def test_route_after_retriever_target_maps_exist_in_compiled_graph():
    """조건부 엣지의 두 목적지 키가 실제 그래프 노드와 연결돼 있는지(오타 방지) 확인합니다."""
    from src.agent.graph import agent_runtime

    nodes = agent_runtime.get_graph().nodes
    assert "quick_responder" in nodes
    assert "report_generator" in nodes


def test_route_after_rewrite_returns_to_quick_response():
    state = {"intent_category": IntentCategory.INFO_LOOKUP.value}
    assert route_after_rewrite(state) == "quick_response"


def test_route_after_rewrite_defaults_to_retrieve():
    state = {"intent_category": IntentCategory.COURSE_RECOMMENDATION.value}
    assert route_after_rewrite(state) == "retrieve"


def test_route_after_rewrite_sends_all_non_recommendation_intents_to_quick_response():
    """회귀 방지: route_after_rewrite 는 예전엔 info_lookup 만 quick_response 로 보내고
    course_info/olle_general_info/other 는 (quick_responder 로 들어갔었음에도) retriever 로
    잘못 돌려보냈습니다(2026-07-24 발견 및 수정 — route_after_location_resolve 와 동일하게
    "course_recommendation 이 아니면 quick_response" 기준으로 통일)."""
    for category in (
        IntentCategory.COURSE_INFO,
        IntentCategory.OLLE_GENERAL_INFO,
        IntentCategory.OTHER,
        IntentCategory.INFO_LOOKUP,
    ):
        state = {"intent_category": category.value}
        assert route_after_rewrite(state) == "quick_response", (
            f"{category.value} 의도는 재작성 후에도 quick_response(tool_agent)로 돌아가야 합니다."
        )
