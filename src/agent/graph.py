from langgraph.graph import StateGraph, END
from src.agent.state import AgentState
from src.models.schema import IntentCategory
from src.agent.nodes import (
    classify_intent_node,
    parse_intent_node,
    resolve_market_location_node,
    evaluate_safety_node,
    retrieve_rag_node,
    generate_report_node,
    check_quality_node,
    rewrite_query_node,
    quick_responder_node,
    tool_executor_node,
    tool_agent_node
)


def route_after_location_resolve(state: AgentState) -> str:
    """의도 분류 결과가 course_recommendation 일 때만 코스 기획서 전체 파이프라인
    (safety_evaluator 이후)으로 진행하고, 그 외 의도(info_lookup / course_info /
    olle_general_info / other)는 모두 가벼운 정보 조회 파이프라인으로 분기합니다.
    course_info / olle_general_info / other 는 기획서 생성 목적이 없으므로
    무거운 full pipeline을 우회하여 quick_responder 로 처리합니다."""
    if state.get("intent_category") == IntentCategory.COURSE_RECOMMENDATION.value:
        return "full_pipeline"
    return "quick_response"


def should_call_tools(state: AgentState) -> str:
    """tool_agent_node 가 실행을 요청한 tool_calls 가 있으면 tool_executor 로,
    최종 대화 답변 작성이 완료되었으면 quality_checker 로 라우팅합니다."""
    if state.get("tool_calls"):
        return "call_tools"
    return "quality_check"


def route_after_rewrite(state: AgentState) -> str:
    """품질 검증 실패로 쿼리를 재작성한 뒤 되돌아갈 노드를, 진입했던 경로(quick_responder vs 코스
    기획서 파이프라인)에 맞춰 그대로 유지합니다. route_after_location_resolve 와 동일하게
    "course_recommendation 이 아니면 quick_responder 경로"를 기준으로 판단합니다 — 예전에는
    info_lookup 하나만 특별 취급해서, course_info/olle_general_info/other 로 분류돼
    quick_responder 로 들어간 요청이 재작성 후 엉뚱하게 retriever(코스 검색)로 잘못 돌아가는
    비일관성이 있었습니다(2026-07-24 발견 및 수정)."""
    if state.get("intent_category") != IntentCategory.COURSE_RECOMMENDATION.value:
        return "quick_response"
    return "retrieve"


def should_continue(state: AgentState) -> str:
    """하이브리드 자율 교정 라우터: 품질 만족 여부 및 실패 횟수(loop_count)에 따라
    1차 직행(tool_agent) 또는 2차 이상 우회(query_rewriter)를 선택합니다.
    """
    report = state.get("quality_report")
    loop_count = state.get("loop_count", 0)
    intent = state.get("intent_category")

    # 1. 품질 검증을 통과했거나 자율 순환 한계(3회) 도달 시 종료
    # report.get("passed", False): "passed" 키가 없는 손상된 report 는 fail-closed 하게
    # 실패로 간주합니다(예전엔 기본값 True 라서 malformed dict 를 통과로 오인했음).
    if report and report.get("passed", False):
        print(f"[+] 품질 검증 통과! (최종 루프 횟수: {loop_count})")
        return "end"
    if loop_count >= 3:
        print(f"[!] 최대 자율 순환 횟수(3회)에 도달하여 현재 단계에서 강제 종료하고 답변을 생성합니다.")
        return "end"

    # 2. 하이브리드 교정 라우팅
    # quick_responder 경로(course_recommendation 이 아닌 4개 의도 전부 — route_after_location_resolve
    # 와 동일한 기준)에서 1차 실패(loop_count < 2)인 경우 쿼리 재작성 없이 tool_agent 로 직행합니다.
    # 예전에는 info_lookup 하나만 이 취급을 받아, course_info/olle_general_info/other 로 들어온
    # 요청은 direct_retry 없이 곧장 rewrite 로 가버리는 비일관성이 있었습니다(2026-07-24 수정).
    if intent != IntentCategory.COURSE_RECOMMENDATION.value and loop_count < 2:
        print(f"[-] [하이브리드 교정 1차] 수치/단위 교정을 위해 tool_agent 로 직행합니다. (현재 루프: {loop_count})")
        return "direct_retry"

    print(f"[-] [하이브리드 교정 2차 이상] 쿼리 전면 재작성을 수행합니다. (현재 루프: {loop_count})")
    return "rewrite"


def build_agent_graph():
    """LangGraph StateGraph 를 조율하여 멀티 툴 연동 및 하이브리드 자율 교정 루프가 탑재된 RAG 에이전트를 빌드합니다."""
    workflow = StateGraph(AgentState)

    # 1. 그래프 노드 추가
    workflow.add_node("intent_classifier", classify_intent_node)
    workflow.add_node("intent_parser", parse_intent_node)
    workflow.add_node("market_location_resolver", resolve_market_location_node)
    workflow.add_node("safety_evaluator", evaluate_safety_node)
    workflow.add_node("retriever", retrieve_rag_node)
    workflow.add_node("report_generator", generate_report_node)
    workflow.add_node("quality_checker", check_quality_node)
    workflow.add_node("query_rewriter", rewrite_query_node)
    workflow.add_node("quick_responder", quick_responder_node)
    workflow.add_node("tool_executor", tool_executor_node)
    workflow.add_node("tool_agent", tool_agent_node)

    # 2. 고정 경로 엣지 연결
    workflow.set_entry_point("intent_classifier")
    workflow.add_edge("intent_classifier", "intent_parser")
    workflow.add_edge("intent_parser", "market_location_resolver")
    workflow.add_edge("safety_evaluator", "retriever")
    # docent_generator/report_finalizer(당시 이름 local_recommender) 두 노드가 report_generator
    # 하나로 통합되면서(2026-07-24), course_recommendation 의도만 도달하는 이 경로에 있던
    # should_finalize_report 조건부 분기(둘 다 도달하면 사실상 항상 finalize 였음)가 불필요해져
    # 고정 엣지로 단순화됨.
    workflow.add_edge("retriever", "report_generator")
    workflow.add_edge("report_generator", "quality_checker")
    workflow.add_edge("quick_responder", "tool_agent")
    workflow.add_edge("tool_executor", "tool_agent")

    # 2-1. course_recommendation 의도만 full pipeline, 나머지는 quick_responder 로 우회
    workflow.add_conditional_edges(
        "market_location_resolver",
        route_after_location_resolve,
        {
            "quick_response": "quick_responder",
            "full_pipeline": "safety_evaluator"
        }
    )

    # 2-2. tool_agent 툴 호출 루프 연결
    workflow.add_conditional_edges(
        "tool_agent",
        should_call_tools,
        {
            "call_tools": "tool_executor",
            "quality_check": "quality_checker"
        }
    )

    # 4. 하이브리드 품질 검증 기반 조건부 분기 연결
    workflow.add_conditional_edges(
        "quality_checker",
        should_continue,
        {
            "end": END,
            "direct_retry": "tool_agent",
            "rewrite": "query_rewriter"
        }
    )

    # 5. 2차 자율 피드백 루프 연결 - 진입했던 경로로 되돌아감
    workflow.add_conditional_edges(
        "query_rewriter",
        route_after_rewrite,
        {
            "quick_response": "tool_agent",
            "retrieve": "retriever"
        }
    )

    # 6. 그래프 컴파일
    app = workflow.compile()
    return app


# 애플리케이션 싱글톤 런타임 객체 노출
agent_runtime = build_agent_graph()
