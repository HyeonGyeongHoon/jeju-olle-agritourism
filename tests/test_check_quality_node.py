import json
from unittest.mock import patch

from src.agent import nodes
from src.agent.graph import should_continue
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


def test_check_quality_node_short_circuits_when_retriever_exited_early():
    """정책 전환(2026-07-25 무조건 반려): retrieve_rag_node 가 DB 매칭 0건으로 검색을 중단한
    경우(is_exit_early)는 다른 어떤 분기보다 먼저 통과시켜야 합니다. 최종 답변이 결정론적인 반려
    메시지라 검증할 사실이 없는데도, retrieve_rag_node 가 이미 조회해둔 market_insight 가 상태에
    남아있으면 "코스 청크는 없지만 culture/market 은 있는" 분기를 타서(quick_responder 경로와 동일
    로직) 반려 메시지를 무관한 통계 컨텍스트와 대조하다 최대 3회 재작성 루프를 돕니다."""
    state = {
        "query": "대정읍 마늘 코스로 기획서 만들어줘",
        "is_exit_early": True,
        "exit_reason": "'대정읍' 지역과 직접 겹치는 올레 코스를 찾지 못해 기획서를 생성할 수 없습니다.",
        "retrieved_chunks": [],
        "culture_chunks": [],
        # retrieve_rag_node 가 반려 전에 이미 조회를 마쳐 상태에 남겨둔 통계 (이게 있어도 검증
        # 분기로 빠지지 않아야 함)
        "market_insight": {
            "region_dong": "대정읍", "year_month": "2026-05", "total_visitors": 12000,
            "yoy_growth_rate": None, "foreign_visitors": None, "female_ratio": None,
            "young_2030_ratio": None, "middle_4060_ratio": None,
            "senior_70s_ratio": None,
        },
        "final_response": "요청하신 조건으로는 기획서를 작성할 수 없습니다. '대정읍' 지역과...",
        "b2b_params": {
            "key_item_or_crop": "마늘", "preferred_location": "대정읍", "target_month": 3,
        },
    }

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        result = check_quality_node(state)

    mock_llm.assert_not_called()
    assert result["quality_report"]["passed"] is True
    assert result["quality_report"]["score"] == 1.0
    # 반려 응답은 재작성해도 달라지지 않으므로 should_continue 가 곧바로 end 로 보내야 합니다.
    assert should_continue({**state, **result, "loop_count": 0}) == "end"


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


def test_check_quality_node_includes_market_insight_and_culture_context_when_chunks_present():
    """회귀 방지: 코스 청크(retrieved_chunks)가 있는 분기(기획서 경로)는 예전엔 코스 사실관계만
    검증 컨텍스트에 넣고 market_insight/culture_chunks 는 전혀 포함하지 않았습니다. 그 결과
    report_generator가 Market Insight 수치를 정확히 인용해도 검증 LLM이 "컨텍스트에 없는 수치"로
    오인해 오탐 반려(failed)를 내고 불필요한 query_rewriter 재시도를 유발했습니다. 이 분기에도
    market_insight/culture_chunks 가 (quick_responder 경로와 동일하게) 검증 컨텍스트에 포함되어야
    합니다."""
    state = {
        "query": "한림 기획서 만들어줘",
        "retrieved_chunks": [
            {"course_name": "14코스", "total_distance_km": 19.4, "estimated_time_text": "6시간",
             "difficulty": "중", "crops": "감귤,양배추", "administrative_areas": "월령리", "content": "..."}
        ],
        "culture_chunks": [
            {"title": "감귤 생육 정보", "content": "가을철 수확이 한창입니다.",
             "target_crop": "감귤", "crop_name": None, "region_tag": "한림읍",
             "season_stage": "수확기", "active_months": [10, 11]}
        ],
        "market_insight": {
            "region_dong": "한림읍", "year_month": "2026-05", "total_visitors": 45123,
            "yoy_growth_rate": 3.2, "female_ratio": None, "male_ratio": None,
            "youth_10s_ratio": None, "young_2030_ratio": None, "middle_4060_ratio": None,
            "senior_70s_ratio": None, "foreign_visitors": None,
        },
        "final_response": "한림읍 2026-05 방문객 45,123명을 근거로 감귤 테마 상품을 제안합니다.",
        "b2b_params": {"target_month": 10},
    }

    with patch.object(nodes, "get_chat_completion", return_value=_report()) as mock_llm:
        result = check_quality_node(state)

    mock_llm.assert_called_once()
    system_prompt, user_msg = mock_llm.call_args[0]
    # Market Insight 수치와 문화지식 근거가 코스-청크 분기의 검증 컨텍스트에도 포함되어야 함
    assert "45,123명" in user_msg
    assert "가을철 수확이 한창입니다" in user_msg
    assert "Market Insight" in system_prompt
    assert result["quality_report"]["passed"] is True


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
