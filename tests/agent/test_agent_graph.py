import os

import pytest

from src.agent.graph import agent_runtime
from src.agent.weather_client import assess_weather_risk_from_query

# dummy 환경 변수 설정 시 실제 API 와 DB 가 필요한 시나리오 테스트 건너뛰기
is_dummy = (
    "dummy" in os.getenv("SUPABASE_URL", "")
    or "dummy" in os.getenv("UPSTAGE_API_KEY", "")
)
if is_dummy:
    pytestmark = pytest.mark.skip("실제 API 키 및 DB 연결이 필요한 통합 시나리오 테스트이므로 건너뜁니다.")



def test_intent_parsing_and_graph_execution():
    """LangGraph RAG 에이전트에 질의를 주입하여 정상적으로 파싱되고 최종 답변까지 도달하는지 시나리오 테스트합니다."""
    inputs = {
        "query": "구좌읍 당근 밭길을 걷고 싶은데 쉬운 코스 추천해줘",
        "loop_count": 0,
        "parsed_constraints": None,
        "intent_category": "course_recommendation",
        "weather_info": None,
        "safety_check": None,
        "retrieved_chunks": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "docent_answer": None,
        "recommendations": [],
        "final_response": None,
        "quality_report": None
    }
    
    # 1. 그래프 동기 실행
    result = agent_runtime.invoke(inputs)
    
    # 2. 결과 검증
    assert result["parsed_constraints"] is not None
    assert "hard_constraints" in result["parsed_constraints"]
    
    # 휠체어 요구사항 파싱 검증
    assert result["parsed_constraints"]["hard_constraints"]["wheelchair_required"] is False
    
    # RAG 검색 결과가 있는 경우 최종 답변 완성 검증
    assert result["final_response"] is not None
    assert "## 5. 🎒 로컬 안전 탐방 가이드 및 준비물" in result["final_response"]
    assert "## 6. 🛡️ Trust Tagging" in result["final_response"]
    assert "제주 안전·에티켓 가이드 DB" in result["final_response"]


def test_safety_evaluator_weather_fallback():
    """태풍 등 강풍 경보 발생 시 Safety Evaluator 가 작동하여 안전 우회 경로 보정 쿼리를 수립하는지 검증합니다.
    safety_evaluator 는 이제 course_recommendation 의도일 때만 실행되는 full pipeline 에만 있으므로
    (route_after_location_resolve — 그 외 4개 의도는 quick_responder 로 우회), intent_category 를
    course_recommendation 으로 명시해 LLM 분류 결과에 좌우되지 않고 결정적으로 그 경로를 태웁니다."""
    query = "태풍 불 때 올레길 1코스 걷는 것 괜찮을까?"
    weather = assess_weather_risk_from_query(query)

    assert weather["status"] == "DANGER"
    assert weather["warnings"]

    inputs = {
        "query": query,
        "loop_count": 0,
        "parsed_constraints": None,
        "intent_category": "course_recommendation",
        "weather_info": None,
        "safety_check": None,
        "retrieved_chunks": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "docent_answer": None,
        "recommendations": [],
        "final_response": None,
        "quality_report": None
    }

    result = agent_runtime.invoke(inputs)
    
    # Safety Evaluator 가 DANGER 일 때 무조건 반려(Fail-Fast)가 가동되는지 검증
    assert result["safety_check"] is not None
    assert result["safety_check"]["reroute_required"] is True
    assert result["is_exit_early"] is True
    assert "기상 악화" in result["exit_reason"]


def test_quick_responder_generic_safety_query_no_crop_contamination():
    """일반 올레길 안전 수칙 질문 시, 무관한 작물 정보(수박/참외 등)나 시스템 라벨([대상 코스 DB 실측 메타데이터])이
    답변에 오염/유출되지 않고 올바르게 차단되는지 검증합니다."""
    query = "여름철 올레길 안전 수칙 알려줘"
    inputs = {
        "query": query,
        "loop_count": 0,
        "parsed_constraints": None,
        "intent_category": "info_lookup",
        "weather_info": None,
        "safety_check": None,
        "retrieved_chunks": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "docent_answer": None,
        "recommendations": [],
        "final_response": None,
        "quality_report": None
    }
    
    result = agent_runtime.invoke(inputs)
    
    response = result["final_response"]
    assert response is not None
    
    # 1. 무관한 작물 정보 유출 차단 검증 (문화지식 미조회로 수박/참외/당근 등이 없어야 함)
    assert "수박" not in response
    assert "참외" not in response
    assert "당근" not in response
    
    # 2. 시스템 내부 라벨 유출 차단 검증
    assert "DB 실측" not in response
    assert "메타데이터" not in response
    assert "[대상 코스" not in response

    # 3. 실제 DB 안전 수칙 콘텐츠 반영 검증
    assert any(k in response for k in ["오전 9시", "9시", "지킴이", "단말기", "콜센터", "자제", "오후 6시", "오후 5시"])
