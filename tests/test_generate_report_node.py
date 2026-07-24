import threading
import time
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


def test_generate_report_node_calls_visit_jeju_api_concurrently_not_sequentially():
    """회귀 방지: 예전엔 작물x지역 조합마다 비짓제주 API를 하나씩 순서대로 호출해서,
    조합 수만큼 지연이 그대로 누적됐습니다(조합 N개 x 개별 호출 시간). 이제는 스레드풀로
    동시에 조회하므로, 조합이 여러 개여도 총 소요 시간이 순차 합산보다 훨씬 짧아야 합니다."""
    call_log = []
    lock = threading.Lock()

    def slow_recommendation(crop, area):
        time.sleep(0.2)
        with lock:
            call_log.append((crop, area))
        return [{
            "crop_tag": crop, "title": f"{crop}-{area} 매장",
            "introduction": f"{crop} 전문점", "source": "mock_db",
        }]

    state = _base_state(retrieved_chunks=[
        {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
         "difficulty": "중", "crops": "감귤,당근", "administrative_areas": "종달리", "content": "..."},
        {"course_name": "2코스", "total_distance_km": 10.0, "estimated_time_text": "3시간",
         "difficulty": "하", "crops": "마늘", "administrative_areas": "신평리", "content": "..."},
    ])

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 푸드/음료 | ... | ... | ... |"],
    ), patch.object(nodes, "get_visit_jeju_recommendations", side_effect=slow_recommendation):
        start = time.monotonic()
        result = generate_report_node(state)
        elapsed = time.monotonic() - start

    # (감귤,종달리), (당근,종달리), (마늘,신평리) - 중복 없는 조합 3개만 호출되어야 함
    assert len(call_log) == 3
    # 순차 호출이었다면 3 x 0.2초 = 0.6초 이상 걸렸어야 하지만, 동시 호출이면 훨씬 짧아야 함
    assert elapsed < 0.5
    assert len(result["recommendations"]) == 3


def test_generate_report_node_deduplicates_visit_jeju_calls_across_chunks():
    """같은 (작물, 지역) 조합이 여러 코스에서 반복되면, 비짓제주 API는 조합당 딱 한 번만
    호출되어야 합니다(스레드풀로 바꾸면서도 기존 캐싱 동작이 깨지지 않았는지 확인)."""
    call_count = {"n": 0}

    def counting_recommendation(crop, area):
        call_count["n"] += 1
        return [{"crop_tag": crop, "title": "매장", "introduction": "소개", "source": "mock_db"}]

    state = _base_state(retrieved_chunks=[
        {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
         "difficulty": "중", "crops": "감귤", "administrative_areas": "종달리", "content": "..."},
        {"course_name": "2코스", "total_distance_km": 10.0, "estimated_time_text": "3시간",
         "difficulty": "하", "crops": "감귤", "administrative_areas": "종달리", "content": "..."},
    ])

    with patch.object(nodes, "get_chat_completion", side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"]), \
         patch.object(nodes, "get_visit_jeju_recommendations", side_effect=counting_recommendation):
        result = generate_report_node(state)

    assert call_count["n"] == 1
    assert len(result["recommendations"]) == 2  # 두 코스 각각에 캐시된 결과가 반영됨
