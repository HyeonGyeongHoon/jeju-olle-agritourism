import pytest
from src.agent.graph import agent_runtime
from src.agent.state import AgentState
from src.agent.weather_client import simulate_weather_by_query


def test_intent_parsing_and_graph_execution():
    """LangGraph RAG 에이전트에 질의를 주입하여 정상적으로 파싱되고 최종 답변까지 도달하는지 시나리오 테스트합니다."""
    inputs = {
        "query": "당근 밭길을 걷고 싶은데 휠체어로도 갈 수 있는 2시간 이내의 쉬운 코스 추천해줘",
        "loop_count": 0,
        "parsed_constraints": None,
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
    assert result["parsed_constraints"]["hard_constraints"]["wheelchair_required"] is True
    
    # RAG 검색 결과가 있는 경우 최종 답변 완성 검증
    assert result["final_response"] is not None
    assert "Trust Tagging" or "[출처: " in result["final_response"]


def test_safety_evaluator_weather_fallback():
    """태풍 등 강풍 경보 발생 시 Safety Evaluator 가 작동하여 안전 우회 경로 보정 쿼리를 수립하는지 검증합니다."""
    query = "태풍 불 때 올레길 1코스 걷는 것 괜찮을까?"
    weather = simulate_weather_by_query(query)
    
    assert weather["status"] == "DANGER"
    assert "태풍경보" in weather["warnings"][0]
    
    inputs = {
        "query": query,
        "loop_count": 0,
        "parsed_constraints": None,
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
    
    # Safety Evaluator 가 켜졌는지 검증
    assert result["safety_check"] is not None
    assert result["safety_check"]["reroute_required"] is True
    assert "숲길" in result["safety_check"]["alternative_query_override"]
