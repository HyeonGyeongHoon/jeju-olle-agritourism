from src.agent.graph import route_after_location_resolve, route_after_rewrite
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
