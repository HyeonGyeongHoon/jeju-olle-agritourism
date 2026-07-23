from langgraph.graph import StateGraph, END
from src.agent.state import AgentState
from src.models.schema import IntentCategory
from src.agent.nodes import (
    route_intent_node,
    parse_intent_node,
    resolve_market_location_node,
    evaluate_safety_node,
    retrieve_rag_node,
    generate_docent_node,
    recommend_local_node,
    check_quality_node,
    rewrite_query_node
)


def should_recommend_local(state: AgentState) -> str:
    """의도 분류 결과가 코스 추천(course_recommendation)일 때만 로컬 맛집/카페 추천 노드를 실행하도록 분기합니다."""
    if state.get("intent_category") == IntentCategory.COURSE_RECOMMENDATION.value:
        return "recommend"
    return "skip"


def should_continue(state: AgentState) -> str:
    """품질 만족 여부 또는 최대 자율 순환 한계 횟수에 도달했는지 판단하여 갈림길을 라우팅합니다."""
    report = state.get("quality_report")
    loop_count = state.get("loop_count", 0)
    
    # 1. 품질 검증을 통과했거나, 자율 순환을 3회 이상 수행한 경우 최종 종료 처리
    if report and report.get("passed", True):
        print(f"[+] 품질 검증 통과! (최종 루프 횟수: {loop_count})")
        return "end"
    if loop_count >= 3:
        print(f"[!] 최대 자율 순환 횟수(3회)에 도달하여 현재 단계에서 강제 종료하고 답변을 생성합니다.")
        return "end"
        
    print(f"[-] 품질 만족 실패. 쿼리 재작성을 거쳐 재검색을 진행합니다. (현재 루프 횟수: {loop_count})")
    return "rewrite"


def build_agent_graph():
    """LangGraph StateGraph 를 조율하여 자율 교정 루프가 탑재된 RAG 에이전트를 빌드합니다."""
    workflow = StateGraph(AgentState)
    
    # 1. 그래프 노드 추가
    workflow.add_node("intent_router", route_intent_node)
    workflow.add_node("intent_parser", parse_intent_node)
    workflow.add_node("market_location_resolver", resolve_market_location_node)
    workflow.add_node("safety_evaluator", evaluate_safety_node)
    workflow.add_node("retriever", retrieve_rag_node)
    workflow.add_node("docent_generator", generate_docent_node)
    workflow.add_node("local_recommender", recommend_local_node)
    workflow.add_node("quality_checker", check_quality_node)
    workflow.add_node("query_rewriter", rewrite_query_node)

    # 2. 고정 경로 엣지 연결
    workflow.set_entry_point("intent_router")
    workflow.add_edge("intent_router", "intent_parser")
    workflow.add_edge("intent_parser", "market_location_resolver")
    workflow.add_edge("market_location_resolver", "safety_evaluator")
    workflow.add_edge("safety_evaluator", "retriever")
    workflow.add_edge("retriever", "docent_generator")
    workflow.add_edge("local_recommender", "quality_checker")

    # 3. 의도 분류 기반 조건부 분기 - 코스 추천 의도일 때만 로컬 맛집/카페 추천 노드 실행
    workflow.add_conditional_edges(
        "docent_generator",
        should_recommend_local,
        {
            "recommend": "local_recommender",
            "skip": "quality_checker"
        }
    )

    # 4. 품질 검증 기반 조건부 분기(Conditional Edge) 연결
    workflow.add_conditional_edges(
        "quality_checker",
        should_continue,
        {
            "end": END,
            "rewrite": "query_rewriter"
        }
    )
    
    # 5. 자율 피드백 루프 연결
    workflow.add_edge("query_rewriter", "retriever")

    # 6. 그래프 컴파일
    app = workflow.compile()
    return app


# 애플리케이션 싱글톤 런타임 객체 노출
agent_runtime = build_agent_graph()
