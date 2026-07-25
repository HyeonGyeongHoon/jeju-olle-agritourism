from unittest.mock import patch

import pytest
from src.agent import nodes
from src.agent.state import AgentState
from src.agent.nodes import (
    classify_intent_node,
    tool_executor_node,
    tool_agent_node
)
from src.agent.graph import should_continue, should_call_tools
from src.models.schema import IntentCategory


def test_state_cleanup_in_classifier():
    """classify_intent_node 진입 시 이전 Turn 의 tool_calls, tool_outputs, quality_report, tool_depth 가 정결하게 초기화되는지 검증합니다."""
    dirty_state: AgentState = {
        "query": "구좌읍 5월 방문객 수 알려줘",
        "intent_category": None,
        "tool_calls": [{"name": "retrieve_visitor_statistics_tool"}],
        "tool_outputs": [{"result": "128400명"}],
        "quality_report": {"passed": False, "feedback": "수치 왼곡"},
        "tool_depth": 2,
    }
    res = classify_intent_node(dirty_state)
    assert res.get("tool_calls") is None
    assert res.get("tool_outputs") is None
    assert res.get("quality_report") is None
    assert res.get("tool_depth") == 0


def test_classify_intent_node_ignores_invalid_preset_category_and_reclassifies():
    """회귀 방지: 호출부가 IntentCategory enum 에 없는 값(오타/구버전 카테고리명 등)을
    intent_category 로 미리 채워 넘겨도, 예전처럼 그대로 신뢰하지 말고 route_intent 로
    다시 분류해야 합니다."""
    state: AgentState = {
        "query": "감귤 수확 시기가 언제야?",
        "intent_category": "info_lookup_node",  # enum 에 없는 구버전/오타 값
    }
    with patch.object(
        nodes, "route_intent",
        return_value=type("R", (), {"category": IntentCategory.INFO_LOOKUP, "target_course": None})(),
    ) as mock_route:
        res = classify_intent_node(state)

    mock_route.assert_called_once()
    assert res["intent_category"] == IntentCategory.INFO_LOOKUP.value


def test_classify_intent_node_trusts_valid_preset_category():
    """호출부가 IntentCategory enum 에 속하는 유효한 값을 미리 채워 넘기면, 그대로
    신뢰하고 route_intent(LLM 호출)를 다시 부르지 않아야 합니다."""
    state: AgentState = {
        "query": "구좌읍 5월 방문객 수 알려줘",
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    with patch.object(nodes, "route_intent") as mock_route:
        res = classify_intent_node(state)

    mock_route.assert_not_called()
    assert res["intent_category"] == IntentCategory.INFO_LOOKUP.value


def test_tool_executor_node_multi_call():
    """tool_executor_node 가 방문객 통계 도구와 작물 지식 도구의 다중/병렬 tool_calls 목록을 받아 각각 실행하는지 검증합니다."""
    state: AgentState = {
        "query": "구좌 당근과 구좌읍 통계 알려줘",
        "tool_calls": [
            {
                "name": "retrieve_visitor_statistics_tool",
                "args": {"region_dong": "구좌읍", "year_month": "2026-05", "metric": "total_visitors"}
            },
            {
                "name": "retrieve_culture_crop_knowledge_tool",
                "args": {"keyword_or_crop": "당근"}
            }
        ],
        "tool_outputs": [],
        "tool_depth": 0,
    }
    res = tool_executor_node(state)
    outputs = res.get("tool_outputs") or []
    assert len(outputs) == 2
    assert res.get("tool_depth") == 1
    assert outputs[0]["tool_name"] == "retrieve_visitor_statistics_tool"
    assert "[조회 성공]" in outputs[0]["result"]
    assert outputs[1]["tool_name"] == "retrieve_culture_crop_knowledge_tool"
    assert "[지식 조회 성공" in outputs[1]["result"]


def test_tool_agent_max_depth_guard():
    """tool_depth 가 3회 이상(max depth)에 도달하면 tool_agent_node 가 더 이상 툴을 부르지 않고(tool_calls=None) 작성을 강제 완료하는지 검증합니다."""
    state: AgentState = {
        "query": "구좌읍 통계 알려줘",
        "tool_outputs": [{"tool_name": "retrieve_visitor_statistics_tool", "result": "128,400명"}],
        "tool_depth": 3,
        "quality_report": None,
    }
    res = tool_agent_node(state)
    assert res.get("tool_calls") is None
    assert res.get("final_response") is not None


def test_tool_agent_node_skips_tool_calls_for_other_intent():
    """사용자 요청: "당근 코스 추천해줘"처럼 intent_category가 "other"인 질의는,
    quick_responder_node가 이미 서비스 범위 안내(코스 추천 미제공) 답변을 만들어뒀으므로,
    tool_agent_node가 b2b_params의 key_item_or_crop 등을 보고 자동으로 도구를 호출해 그
    결정을 뒤엎지 않아야 합니다."""
    state: AgentState = {
        "query": "당근 코스 추천해줘",
        "intent_category": "other",
        "tool_outputs": [],
        "tool_depth": 0,
        "quality_report": None,
        "b2b_params": {"key_item_or_crop": "당근", "preferred_location": None, "market_location_query": None},
        "final_response": "이 서비스는 개별 코스를 추천해 드리지 않고...",
    }

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        res = tool_agent_node(state)

    mock_llm.assert_not_called()
    assert res.get("tool_calls") is None


def test_hybrid_correction_routing():
    """should_continue 라우터가 1차 실패(loop_count < 2) 시 direct_retry(interactive_agent), 2차 이상 실패 시 rewrite(query_rewriter)로 분기하는지 검증합니다."""
    # 1차 실패 케이스
    state_loop1: AgentState = {
        "quality_report": {"passed": False, "score": 0.5, "feedback": "단위 오기"},
        "loop_count": 1,
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    assert should_continue(state_loop1) == "direct_retry"

    # 2차 실패 케이스
    state_loop2: AgentState = {
        "quality_report": {"passed": False, "score": 0.3, "feedback": "키워드 꼬임"},
        "loop_count": 2,
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    assert should_continue(state_loop2) == "rewrite"

    # 성공 케이스
    state_passed: AgentState = {
        "quality_report": {"passed": True, "score": 1.0},
        "loop_count": 1,
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    assert should_continue(state_passed) == "end"


def test_hybrid_correction_routing_applies_to_all_non_recommendation_intents():
    """회귀 방지: should_continue 는 예전엔 info_lookup 만 direct_retry 대상으로 취급했는데,
    quick_responder 로 들어가는 나머지 의도(course_info/olle_general_info/other)는 direct_retry
    없이 곧장 rewrite 로 가버리는 비일관성이 있었습니다(2026-07-24 발견 및 수정 —
    route_after_location_resolve 와 동일하게 "course_recommendation 이 아니면" 기준으로 통일)."""
    for category in (
        IntentCategory.COURSE_INFO,
        IntentCategory.OLLE_GENERAL_INFO,
        IntentCategory.OTHER,
        IntentCategory.INFO_LOOKUP,
    ):
        state_loop1 = {
            "quality_report": {"passed": False, "score": 0.5, "feedback": "오류"},
            "loop_count": 1,
            "intent_category": category.value,
        }
        assert should_continue(state_loop1) == "direct_retry", (
            f"{category.value} 의도는 loop_count<2 에서 direct_retry 로 가야 합니다."
        )

        state_loop2 = {
            "quality_report": {"passed": False, "score": 0.3, "feedback": "오류"},
            "loop_count": 2,
            "intent_category": category.value,
        }
        assert should_continue(state_loop2) == "rewrite", (
            f"{category.value} 의도는 loop_count>=2 에서 rewrite 로 가야 합니다."
        )


def test_hybrid_correction_routing_course_recommendation_always_rewrites():
    """course_recommendation 의도는 quick_responder/tool_agent 경로 자체를 타지 않으므로,
    실패 시 loop_count 와 무관하게 항상 rewrite(query_rewriter)로 가야 합니다(direct_retry 대상 아님)."""
    state = {
        "quality_report": {"passed": False, "score": 0.5, "feedback": "오류"},
        "loop_count": 0,
        "intent_category": IntentCategory.COURSE_RECOMMENDATION.value,
    }
    assert should_continue(state) == "rewrite"


def test_tool_agent_increments_loop_count_on_quality_retry():
    """direct_retry 로 돌아왔을 때(quality_report 가 실패 상태) loop_count 를 1 증가시켜야
    quality_checker <-> tool_agent_node 사이의 무한 루프를 막을 수 있습니다(회귀 방지 —
    이 노드는 원래 loop_count 를 전혀 건드리지 않아, quality_report 가 계속 실패하면 loop_count
    가 0에 고정된 채 should_continue 의 "loop_count < 2" 조건이 영원히 참이 되는 버그가 있었음)."""
    state: AgentState = {
        "query": "구좌읍 통계 알려줘",
        "tool_outputs": [{"tool_name": "retrieve_visitor_statistics_tool", "result": "128,400명"}],
        "tool_depth": 1,
        "quality_report": {"passed": False, "feedback": "수치 오류"},
        "loop_count": 1,
    }
    with patch.object(nodes, "get_chat_completion", return_value="수정된 답변"):
        res = tool_agent_node(state)

    assert res.get("loop_count") == 2


def test_tool_agent_does_not_touch_loop_count_on_first_pass():
    """진질반(quality_report 가 없는 시)에는 loop_count 를 건드리지 않아야
    합니다 - 재시도가 아니라 정상적인 첫 답변 생성이기 때문입니다."""
    state: AgentState = {
        "query": "구좌읍 통계 알려줘",
        "tool_outputs": [{"tool_name": "retrieve_visitor_statistics_tool", "result": "128,400명"}],
        "tool_depth": 1,
        "quality_report": None,
        "loop_count": 0,
    }
    with patch.object(nodes, "get_chat_completion", return_value="첫 답변"):
        res = tool_agent_node(state)

    assert "loop_count" not in res


def test_tool_agent_increments_loop_count_at_max_depth_retry():
    """tool_depth 한도 도달 방어 분기(depth>=3)에서도 재시도 상황이면 loop_count 를 증가시켜야
    합니다 - 이 분기도 quality_checker 로 이어지는 최종 답변을 생성하기 때문입니다."""
    state: AgentState = {
        "query": "구좌읍 통계 알려줘",
        "tool_outputs": [{"tool_name": "retrieve_visitor_statistics_tool", "result": "128,400명"}],
        "tool_depth": 3,
        "quality_report": {"passed": False, "feedback": "수치 오류"},
        "loop_count": 1,
    }
    with patch.object(nodes, "get_chat_completion", return_value="최종 답변"):
        res = tool_agent_node(state)

    assert res.get("loop_count") == 2


def test_hybrid_correction_never_loops_forever_once_loop_count_advances():
    """tool_agent_node 가 실제로 loop_count 를 증가시키면, should_continue 는 2회
    실패 이후 반드시 rewrite 경로로 넘어가 direct_retry 무한 루프에 빠지지 않습니다(회귀 방지)."""
    with patch.object(nodes, "get_chat_completion", return_value="답변"):
        state: AgentState = {
            "query": "구좌읍 통계 알려줘",
            "tool_outputs": [{"tool_name": "x", "result": "y"}],
            "tool_depth": 1,
            "quality_report": {"passed": False, "feedback": "오류"},
            "loop_count": 0,
        }
        loop_count_1 = tool_agent_node(state)["loop_count"]
        assert should_continue({
            "quality_report": {"passed": False},
            "loop_count": loop_count_1,
            "intent_category": IntentCategory.INFO_LOOKUP.value,
        }) == "direct_retry"

        loop_count_2 = tool_agent_node({**state, "loop_count": loop_count_1})["loop_count"]
        assert should_continue({
            "quality_report": {"passed": False},
            "loop_count": loop_count_2,
            "intent_category": IntentCategory.INFO_LOOKUP.value,
        }) == "rewrite"


_COURSE_7_META = {
    "course_name": "7코스",
    "crops": "감귤",
    "administrative_areas": "서홍동,법환동",
    "total_distance_km": 17.6,
    "estimated_time_hours": 6.0,
    "estimated_time_text": "5~6시간",
    "difficulty": "중",
    "start_point": "제주올레 여행자센터",
    "end_point": "월평 아왜낭목",
}


def test_tool_agent_includes_course_meta_and_no_alternative_recommendation_rule():
    """회귀 방지: 최종 사용자 답변은 quick_responder_node 가 아니라 이 노드가 쓰는 경우가
    많은데(도구를 한 번이라도 호출하면 final_response 를 덮어씀), 예전엔 이 노드 프롬프트에
    도구 결과만 들어가 코스 실측치가 최종 답변에 전달되지 않아 "7코스는 비교적 평탄해 초보자도
    좋다"는 근거 없는 답이 나왔습니다. 또 절대규칙 2번의 "가용 옵션 목록 안내"가 특정 코스
    질의에서는 "대신 다른 지역 추천"으로 변질돼 정책을 우회했습니다."""
    state: AgentState = {
        "query": "7코스 초보자한테 추천할 만해?",
        "target_course": "7코스",
        "course_meta": _COURSE_7_META,
        "tool_outputs": [{
            "tool_name": "retrieve_visitor_statistics_tool",
            "result": "[오류] '서홍동'은 올레 코스 경유 지역이 아니어서... 조회 가능한 지역: 강정동, 구좌읍, 성산읍",
        }],
        "tool_depth": 1,
        "quality_report": None,
        "loop_count": 0,
    }
    with patch.object(nodes, "get_chat_completion", return_value="7코스는 17.6km입니다.") as mock_llm:
        tool_agent_node(state)

    system_prompt, user_msg = mock_llm.call_args[0]
    assert "17.6km" in user_msg
    assert "난이도: 중" in user_msg
    assert "다른 코스나 다른 지역을 대안으로 추천하지 마세요" in user_msg
    assert "인용" in system_prompt


def test_tool_agent_includes_course_meta_at_max_depth_branch():
    """depth>=3 방어 분기도 최종 답변을 만들므로 같은 코스 근거/주의가 들어가야 합니다."""
    state: AgentState = {
        "query": "7코스 초보자한테 추천할 만해?",
        "target_course": "7코스",
        "course_meta": _COURSE_7_META,
        "tool_outputs": [{"tool_name": "x", "result": "y"}],
        "tool_depth": 3,
        "quality_report": None,
        "loop_count": 0,
    }
    with patch.object(nodes, "get_chat_completion", return_value="답변") as mock_llm:
        tool_agent_node(state)

    _, user_msg = mock_llm.call_args[0]
    assert "17.6km" in user_msg
    assert "다른 코스나 다른 지역을 대안으로 추천하지 마세요" in user_msg


def test_tool_agent_preserves_existing_answer_when_nothing_new_to_look_up():
    """회귀 방지: 호출할 도구도, 확보된 도구 결과도 없으면 이 노드가 새로 알아낸 정보가 없는데도
    예전엔 컨텍스트가 텅 빈 프롬프트로 재답변을 생성해, quick_responder_node 가 DB 근거로 만든
    답변을 근거 없는 일반 지식 답변으로 덮어썼습니다."""
    state: AgentState = {
        "query": "7코스 초보자한테 추천할 만해?",
        "target_course": "7코스",
        "course_meta": _COURSE_7_META,
        "b2b_params": {"preferred_location": None, "key_item_or_crop": None},
        "tool_outputs": [],
        "tool_depth": 0,
        "quality_report": None,
        "loop_count": 0,
        "final_response": "7코스는 17.6km, 예상 6시간, 난이도 중입니다.",
    }
    with patch.object(nodes, "get_chat_completion") as mock_llm:
        res = tool_agent_node(state)

    mock_llm.assert_not_called()
    assert res == {"tool_calls": None}


def test_tool_agent_still_regenerates_when_previous_answer_had_no_grounding():
    """대조군: 근거를 하나도 찾지 못해 quick_responder 가 "찾지 못했습니다"로 끝낸 경우는
    기존 동작(일반 지식 재답변)을 그대로 유지해야 합니다 — 근거 있는 답변만 보존합니다."""
    state: AgentState = {
        "query": "올레길 준비물 뭐가 있어?",
        "target_course": None,
        "course_meta": None,
        "culture_chunks": [],
        "market_insight": None,
        "b2b_params": {"preferred_location": None, "key_item_or_crop": None},
        "tool_outputs": [],
        "tool_depth": 0,
        "quality_report": None,
        "loop_count": 0,
        "final_response": "죄송합니다. ... 찾지 못했습니다.",
    }
    with patch.object(nodes, "get_chat_completion", return_value="준비물 안내") as mock_llm:
        res = tool_agent_node(state)

    mock_llm.assert_called_once()
    assert res["final_response"] == "준비물 안내"


def test_tool_agent_still_regenerates_on_quality_retry_without_tool_outputs():
    """단, 품질 재시도 경로에서는 loop_count 증가가 필요하므로(무한 루프 방지) 기존 답변 보존
    분기로 빠지지 않고 재생성을 그대로 수행해야 합니다."""
    state: AgentState = {
        "query": "7코스 초보자한테 추천할 만해?",
        "target_course": "7코스",
        "course_meta": _COURSE_7_META,
        "b2b_params": {"preferred_location": None, "key_item_or_crop": None},
        "tool_outputs": [],
        "tool_depth": 0,
        "quality_report": {"passed": False, "feedback": "근거 부족"},
        "loop_count": 0,
        "final_response": "이전 답변",
    }
    with patch.object(nodes, "get_chat_completion", return_value="재생성 답변") as mock_llm:
        res = tool_agent_node(state)

    mock_llm.assert_called_once()
    assert res["loop_count"] == 1
    assert res["final_response"] == "재생성 답변"


def test_should_continue_fails_closed_on_malformed_quality_report():
    """회귀 방지: quality_report 에 "passed" 키가 아예 없는(손상된) dict 가 와도,
    예전처럼 통과(True)로 오인하지 않고 실패로 간주해 교정 루프를 계속 돌아야 합니다."""
    state: AgentState = {
        "quality_report": {"score": 0.9},  # "passed" 키 없음
        "loop_count": 0,
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    assert should_continue(state) == "direct_retry"
