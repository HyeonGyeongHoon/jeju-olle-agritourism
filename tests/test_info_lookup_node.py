from unittest.mock import MagicMock, patch

from src.agent import nodes
from src.agent.nodes import quick_responder_node


def _base_state(**b2b_overrides):
    b2b_params = {
        "key_item_or_crop": None,
        "preferred_location": None,
        "target_month": 5,
        "include_market_insights": True,
        "market_location_resolution": None,
    }
    b2b_params.update(b2b_overrides)
    return {
        "query": "제주 밭담문화가 뭐야?",
        "parsed_constraints": {"vector_query": "밭담문화"},
        "b2b_params": b2b_params,
    }


def test_quick_responder_node_builds_answer_from_culture_and_market():
    culture_chunks = [
        {
            "title": "제주 밭담문화",
            "content": "제주의 밭담은 화산석으로 쌓은 경계 담이다.",
            "target_crop": None,
            "crop_name": None,
            "region_tag": None,
            "season_stage": None,
            "active_months": None,
        }
    ]
    market_insight = {
        "region_dong": "구좌읍",
        "year_month": "2026-05",
        "total_visitors": 12000,
        "yoy_growth_rate": 5.2,
        "female_ratio": None,
        "male_ratio": None,
        "youth_10s_ratio": None,
        "young_2030_ratio": None,
        "middle_4060_ratio": None,
        "senior_70s_ratio": None,
        "foreign_visitors": None,
    }

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=culture_chunks) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=market_insight), \
         patch.object(nodes, "get_chat_completion", return_value="제주 밭담문화는...") as mock_llm:
        result = quick_responder_node(_base_state(preferred_location="구좌읍"))

    assert result["culture_chunks"] == culture_chunks
    assert result["market_insight"] == market_insight
    assert result["docent_answer"] == "제주 밭담문화는..."
    assert result["final_response"] == "제주 밭담문화는..."
    assert "retrieved_chunks" not in result

    mock_search_culture.assert_called_once()
    mock_llm.assert_called_once()
    # 검색된 작물/문화 지식 텍스트가 실제로 LLM 프롬프트에 넘어갔는지 확인
    system_prompt, user_msg = mock_llm.call_args[0]
    assert "제주의 밭담은 화산석으로 쌓은 경계 담이다." in user_msg
    assert "12,000명" in user_msg


def test_quick_responder_node_no_results_returns_apology_without_llm_call():
    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None), \
         patch.object(nodes, "get_chat_completion") as mock_llm:
        result = quick_responder_node(_base_state())

    mock_llm.assert_not_called()
    assert "찾지 못했습니다" in result["final_response"]
    assert result["culture_chunks"] == []
    assert result["market_insight"] is None


def test_quick_responder_node_includes_market_location_resolution_note():
    market_insight = {
        "region_dong": "구좌읍",
        "year_month": "2026-05",
        "total_visitors": 12000,
        "yoy_growth_rate": None,
        "female_ratio": None,
        "male_ratio": None,
        "youth_10s_ratio": None,
        "young_2030_ratio": None,
        "middle_4060_ratio": None,
        "senior_70s_ratio": None,
        "foreign_visitors": None,
    }
    resolution = {
        "region_dong": "구좌읍",
        "metric": "foreign_visitors",
        "value": 5000,
        "year_month": "2026-05",
        "direction": "desc",
    }

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=market_insight), \
         patch.object(nodes, "get_chat_completion", return_value="구좌읍 통계...") as mock_llm:
        quick_responder_node(_base_state(
            preferred_location="구좌읍", market_location_resolution=resolution
        ))

    _, user_msg = mock_llm.call_args[0]
    assert "구좌읍" in user_msg
    assert "외국인" in user_msg
    assert "1위 지역으로 자동 선정" in user_msg


def test_quick_responder_node_skips_market_insight_when_disabled():
    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market, \
         patch.object(nodes, "get_chat_completion", return_value="답변"):
        quick_responder_node(_base_state(include_market_insights=False))

    mock_fetch_market.assert_not_called()


def test_quick_responder_node_scopes_search_by_target_course_when_crop_and_location_missing():
    """회귀 방지: "1코스는 무슨 작물이 유명해?" 처럼 target_course 가 인식된 질의는
    예전엔 target_course 가 완전히 무시되어 일반 키워드 검색과 동일하게 동작했습니다.
    key_item_or_crop/preferred_location 이 비어 있으면 그 코스의 실제 crops/
    administrative_areas 로 검색 조건을 자동 보완해야 합니다."""
    course_meta = {"course_name": "1코스", "crops": "감귤,한라봉", "administrative_areas": "성산읍,표선면"}
    culture_chunks = [
        {"title": "감귤 재배", "content": "감귤은 가을에 수확한다.", "target_crop": "감귤",
         "crop_name": "감귤", "region_tag": None, "season_stage": None, "active_months": None}
    ]
    state = _base_state()
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta) as mock_fetch_course, \
         patch.object(nodes, "_search_culture_knowledge", return_value=culture_chunks) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market, \
         patch.object(nodes, "get_chat_completion", return_value="1코스는 감귤로 유명합니다.") as mock_llm:
        quick_responder_node(state)

    assert mock_fetch_course.call_args[0][1] == "1코스"
    assert mock_search_culture.call_args[0][1] == "감귤"
    assert mock_fetch_market.call_args[0][1] == "성산읍"
    _, user_msg = mock_llm.call_args[0]
    assert "1코스" in user_msg


def test_quick_responder_node_does_not_override_explicit_crop_with_target_course():
    """key_item_or_crop 이 이미 명시적으로 채워져 있으면, target_course 의 재배작물로
    덮어쓰지 않고 사용자가 실제로 물어본 작물을 그대로 검색 조건으로 써야 합니다."""
    course_meta = {"course_name": "1코스", "crops": "감귤,한라봉", "administrative_areas": "성산읍"}
    state = _base_state(key_item_or_crop="당근")
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=None), \
         patch.object(nodes, "get_chat_completion", return_value="답변"):
        quick_responder_node(state)

    assert mock_search_culture.call_args[0][1] == "당근"
