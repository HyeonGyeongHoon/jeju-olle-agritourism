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
