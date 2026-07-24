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
