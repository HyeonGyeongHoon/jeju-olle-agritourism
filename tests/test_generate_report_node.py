from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import generate_report_node


def _base_state(**overrides):
    state = {
        "query": "10월 감귤 테마로 구좌읍 코스 기획서 만들어줘",
        "retrieved_chunks": [
            {
                "course_name": "1코스",
                "total_distance_km": 15.0,
                "estimated_time_text": "5시간",
                "difficulty": "중",
                "crops": "감귤",
                "administrative_areas": "종달리",
                "content": "종달리를 지나는 해안 코스",
            }
        ],
        "culture_chunks": [],
        "sub_segments": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "weather_info": {"description": "선선하고 쾌청한 가을", "warnings": []},
        "safety_check": {"reroute_required": False},
        "market_insight": None,
        "b2b_params": {"target_audience": "family", "include_market_insights": True, "target_month": 10},
    }
    state.update(overrides)
    return state


def test_generate_report_node_returns_early_apology_when_no_chunks():
    state = _base_state(retrieved_chunks=[])

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        result = generate_report_node(state)

    mock_llm.assert_not_called()
    assert "찾을 수 없었습니다" in result["final_response"]
    assert result["docent_answer"] == result["final_response"]


def test_generate_report_node_produces_all_five_sections_in_one_call():
    """docent_generator(섹션 1·2)와 report_finalizer(섹션 3·4·5)가 하나로 통합됐으므로,
    generate_report_node 한 번 호출로 5개 섹션이 모두 포함된 완성된 기획서가 나와야 한다."""
    docent_llm_answer = "## 1. 📊 B2B 상품 개요 & 스펙\n...내용...\n\n## 2. 📍 [타임라인/동선 연계] 로컬 영농 & 문화 도슨트 포인트\n|표|표|표|표|"
    mock_recommendations = [
        {
            "crop_tag": "감귤",
            "title": "테스트 카페",
            "introduction": "감귤 디저트 전문 카페",
            "source": "mock_db",
        }
    ]

    with patch.object(nodes, "get_chat_completion", side_effect=[docent_llm_answer, "| 푸드/음료 | ... | ... | ... |"]) as mock_llm, \
         patch.object(nodes, "get_visit_jeju_recommendations", return_value=mock_recommendations):
        result = generate_report_node(_base_state())

    assert mock_llm.call_count == 2  # 섹션 1·2용 1회 + 섹션 3 아이디어용 1회
    report = result["final_response"]
    for header in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5."):
        assert header in report, f"{header} 섹션이 통합 리포트에 없습니다."
    assert result["recommendations"] == mock_recommendations
    assert result["docent_answer"] == docent_llm_answer


def test_generate_report_node_skips_section_3_ideas_when_no_local_recommendations():
    with patch.object(nodes, "get_chat_completion", return_value="## 1. ...\n## 2. ...") as mock_llm, \
         patch.object(nodes, "get_visit_jeju_recommendations", return_value=[]):
        result = generate_report_node(_base_state())

    mock_llm.assert_called_once()  # 소개 참고자료가 없으면 아이디어 LLM 호출 자체를 생략
    assert "아이디어 제안을 생략합니다" in result["final_response"]
    assert "## 5. 🛡️ Trust Tagging" in result["final_response"]
