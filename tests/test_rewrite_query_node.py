import json
from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import rewrite_query_node


def test_rewrite_query_node_updates_vector_query_and_keeps_hard_constraints():
    state = {
        "query": "당근 밭길 코스 알려줘",
        "parsed_constraints": {
            "hard_constraints": {"wheelchair_required": True},
            "vector_query": "당근 밭길",
        },
        "quality_report": {"passed": False, "feedback": "코스명이 컨텍스트와 다릅니다."},
        "loop_count": 1,
    }
    llm_response = json.dumps({"revised_vector_query": "당근 수확 밭길 올레"})

    with patch.object(nodes, "get_chat_completion", return_value=llm_response):
        result = rewrite_query_node(state)

    updated = result["parsed_constraints"]
    assert updated["vector_query"] == "당근 수확 밭길 올레"
    assert updated["hard_constraints"] == {"wheelchair_required": True}
    assert result["loop_count"] == 2


def test_rewrite_query_node_no_longer_requests_or_stores_soft_constraints():
    """회귀 방지: soft_constraints(시간/거리/난이도)는 B2C 시절 소프트 완화 메커니즘과 함께
    제거된 필드라, query_rewriter 가 더 이상 LLM에게 요청하지도 parsed_constraints 에
    저장하지도 않아야 합니다(2026-07-24 정리 — 그 전엔 하류 코드가 읽지 않는 죽은 값을
    매번 요청/저장만 하고 있었음)."""
    state = {
        "query": "당근 밭길 코스 알려줘",
        "parsed_constraints": {"hard_constraints": {}, "vector_query": "당근 밭길"},
        "quality_report": {"passed": False, "feedback": "오류"},
        "loop_count": 0,
    }
    llm_response = json.dumps({
        "revised_vector_query": "당근 밭길 재검색",
        "revised_soft_constraints": {"max_time_hours": 2, "max_distance_km": 5, "difficulty": "하"},
    })

    with patch.object(nodes, "get_chat_completion", return_value=llm_response) as mock_llm:
        result = rewrite_query_node(state)

    assert "soft_constraints" not in result["parsed_constraints"]
    system_prompt, _ = mock_llm.call_args[0]
    assert "soft_constraints" not in system_prompt


def test_rewrite_query_node_falls_back_to_original_constraints_on_llm_failure():
    state = {
        "query": "당근 밭길 코스 알려줘",
        "parsed_constraints": {"hard_constraints": {"wheelchair_required": False}, "vector_query": "당근 밭길"},
        "quality_report": {"passed": False, "feedback": "오류"},
        "loop_count": 0,
    }

    with patch.object(nodes, "get_chat_completion", side_effect=Exception("LLM 오류")):
        result = rewrite_query_node(state)

    assert result["parsed_constraints"] == state["parsed_constraints"]
    assert result["loop_count"] == 1
