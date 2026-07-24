import json
from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import check_quality_node


def _report(passed=True, score=0.9):
    return json.dumps({"passed": passed, "score": score, "feedback": "ok"})


def test_check_quality_node_short_circuits_when_nothing_retrieved():
    state = {
        "query": "제주 밭담문화가 뭐야?",
        "retrieved_chunks": [],
        "culture_chunks": [],
        "market_insight": None,
        "final_response": "관련 정보를 찾지 못했습니다.",
        "b2b_params": {},
    }

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        result = check_quality_node(state)

    mock_llm.assert_not_called()
    assert result["quality_report"]["passed"] is True


def test_check_quality_node_verifies_culture_and_market_when_no_course_chunks():
    state = {
        "query": "제주 밭담문화가 뭐야?",
        "retrieved_chunks": [],
        "culture_chunks": [
            {"title": "제주 밭담문화", "content": "화산석으로 쌓은 경계 담이다.",
             "target_crop": None, "crop_name": None, "region_tag": None,
             "season_stage": None, "active_months": None}
        ],
        "market_insight": {
            "region_dong": "구좌읍", "year_month": "2026-05", "total_visitors": 12000,
            "yoy_growth_rate": None, "female_ratio": None, "male_ratio": None,
            "youth_10s_ratio": None, "young_2030_ratio": None, "middle_4060_ratio": None,
            "senior_70s_ratio": None, "foreign_visitors": None,
        },
        "final_response": "제주 밭담문화는 화산석으로 쌓은 경계 담입니다.",
        "b2b_params": {"target_month": 5},
    }

    with patch.object(nodes, "get_chat_completion", return_value=_report()) as mock_llm:
        result = check_quality_node(state)

    mock_llm.assert_called_once()
    system_prompt, user_msg = mock_llm.call_args[0]
    assert "화산석으로 쌓은 경계 담이다" in user_msg
    assert "구좌읍" in user_msg
    assert result["quality_report"]["passed"] is True


def test_check_quality_node_uses_course_context_when_chunks_present():
    state = {
        "query": "1코스 알려줘",
        "retrieved_chunks": [
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "감귤", "administrative_areas": "성산읍", "content": "..."}
        ],
        "culture_chunks": [],
        "market_insight": None,
        "final_response": "1코스는 15km 입니다.",
        "b2b_params": {},
    }

    with patch.object(nodes, "get_chat_completion", return_value=_report()) as mock_llm:
        result = check_quality_node(state)

    mock_llm.assert_called_once()
    _, user_msg = mock_llm.call_args[0]
    assert "1코스" in user_msg
    assert result["quality_report"]["passed"] is True


def test_check_quality_node_includes_requested_condition_for_relevance_check():
    """회귀 방지: 코스 경로 품질 검증은 예전엔 내부 일관성(사실관계)만 확인하고, 사용자가
    요청한 작물/지역/월 조건과 실제로 관련 있는 코스를 추천했는지는 전혀 검증하지 않았습니다.
    b2b_params 로 요청 조건이 있으면 그 조건이 검증 컨텍스트/프롬프트에 포함되어야 합니다."""
    state = {
        "query": "당근 밭길 코스 알려줘",
        "retrieved_chunks": [
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "감귤", "administrative_areas": "성산읍", "content": "..."}
        ],
        "culture_chunks": [],
        "market_insight": None,
        "final_response": "1코스는 당근 밭길로 유명합니다.",
        "b2b_params": {"key_item_or_crop": "당근", "preferred_location": "구좌읍", "target_month": 3},
    }

    with patch.object(nodes, "get_chat_completion", return_value=_report()) as mock_llm:
        check_quality_node(state)

    mock_llm.assert_called_once()
    system_prompt, user_msg = mock_llm.call_args[0]
    assert "당근" in user_msg
    assert "구좌읍" in user_msg
    assert "요청" in system_prompt


def test_check_quality_node_marks_no_condition_when_b2b_params_empty():
    """b2b_params 에 작물/지역/월 조건이 전혀 없으면, 관련성 검증 항목이 항상 통과로
    간주된다는 안내 문구가 컨텍스트에 그대로 들어가야 합니다(조건 없음을 명시)."""
    state = {
        "query": "1코스 알려줘",
        "retrieved_chunks": [
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "감귤", "administrative_areas": "성산읍", "content": "..."}
        ],
        "culture_chunks": [],
        "market_insight": None,
        "final_response": "1코스는 15km 입니다.",
        "b2b_params": {},
    }

    with patch.object(nodes, "get_chat_completion", return_value=_report()) as mock_llm:
        check_quality_node(state)

    _, user_msg = mock_llm.call_args[0]
    assert "특정 작물/지역/월 조건 없음" in user_msg
