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


def test_should_continue_fails_closed_on_malformed_quality_report():
    """회귀 방지: quality_report 에 "passed" 키가 아예 없는(손상된) dict 가 와도,
    예전처럼 통과(True)로 오인하지 않고 실패로 간주해 교정 루프를 계속 돌아야 합니다."""
    state: AgentState = {
        "quality_report": {"score": 0.9},  # "passed" 키 없음
        "loop_count": 0,
        "intent_category": IntentCategory.INFO_LOOKUP.value,
    }
    assert should_continue(state) == "direct_retry"
