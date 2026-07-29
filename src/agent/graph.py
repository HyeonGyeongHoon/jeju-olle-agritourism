from langgraph.graph import END, StateGraph

# pyrefly: ignore [missing-import]
from src.agent.nodes import (
    analyze_intent_node,
    check_quality_node,
    evaluate_safety_node,
    generate_report_node,
    quick_responder_node,
    retrieve_rag_node,
    rewrite_query_node,
    tool_agent_node,
    tool_executor_node,
)

# pyrefly: ignore [missing-import]
from src.agent.state import AgentState

# pyrefly: ignore [missing-import]
from src.models.schema import IntentCategory


def route_after_quick_response(state: AgentState) -> str:
    """quick_responder 이후, skip_quality_check 가 True 로 설정되어 있으면
    더 이상의 추가 도구 호출이나 품질 검증 루프가 불필요하므로 즉시 END 로 종료합니다.
    그렇지 않은 경우에만 tool_agent 로 라우팅하여 도구 호출 결정을 위임합니다."""
    if state.get("skip_quality_check"):
        return "end"
    return "tool_agent"


def route_after_analysis(state: AgentState) -> str:
    """intent_analyzer 이후, 의도 분류 결과가 course_recommendation 일 때만 B2B 상품 기획서
    전체 파이프라인(safety_evaluator)으로 진행하고, 그 외 의도(info_lookup / other)는 모두
    quick_responder 로 분기합니다."""
    if state.get("intent_category") == IntentCategory.COURSE_RECOMMENDATION.value:
        return "full_pipeline"
    return "quick_response"


def route_after_retriever(state: AgentState) -> str:
    """retriever 가 무조건 반려(Fail-Fast) 정책으로 검색을 중단했는지(is_exit_early) 검사해,
    중단된 경우 report_generator 를 아예 호출하지 않고 quick_responder 로 제어를 넘겨 반려 사유만
    반환하게 합니다(2026-07-25 정책 전환). retrieve_rag_node 의 세 필터(target_course /
    preferred_location / key_item_or_crop) 중 하나라도 DB 매칭이 0건이면 그 노드가 벡터·문화지식
    검색까지 전부 건너뛴 채 이 플래그를 세우므로, 여기서 기획서 생성 경로를 끊어 유료 LLM 호출과
    quality_checker 재작성 루프까지 함께 절약합니다."""
    if state.get("is_exit_early"):
        return "exit_early"
    return "generate_report"


def should_call_tools(state: AgentState) -> str:
    """tool_agent_node 가 실행을 요청한 tool_calls 가 있으면 tool_executor 로,
    quick_responder 가 이미 DB 근거 기반 답변을 만들어 tool_agent 가 조기 종료한 경우
    (skip_quality_check=True)에는 quality_checker 를 건너뛰고 바로 종료합니다.
    그 외 최종 대화 답변 작성이 완료되었으면 quality_checker 로 라우팅합니다."""
    if state.get("tool_calls"):
        return "call_tools"
    if state.get("skip_quality_check"):
        return "end"
    return "quality_check"


def route_after_rewrite(state: AgentState) -> str:
    """품질 검증 실패로 쿼리를 재작성한 뒤 되돌아갈 노드를, 진입했던 경로(quick_responder vs 코스
    기획서 파이프라인)에 맞춰 그대로 유지합니다. route_after_location_resolve 와 동일하게
    "course_recommendation 이 아니면 quick_responder 경로"를 기준으로 판단합니다 — 예전에는
    info_lookup 하나만 특별 취급해서, other 로 분류돼 quick_responder 로 들어간 요청이
    재작성 후 엉뚱하게 retriever(코스 검색)로 잘못 돌아가는 비일관성이 있었습니다
    (2026-07-24 발견 및 수정)."""
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
        print("[!] 최대 자율 순환 횟수(3회)에 도달하여 현재 단계에서 강제 종료하고 답변을 생성합니다.")
        return "end"

    # 2. 하이브리드 교정 라우팅
    # quick_responder 경로(course_recommendation 이 아닌 4개 의도 전부 — route_after_location_resolve
    # 와 동일한 기준)에서 1차 실패(loop_count < 2)인 경우 쿼리 재작성 없이 tool_agent 로 직행합니다.
    # 예전에는 info_lookup 하나만 이 취급을 받아, other 로 들어온 요청은 direct_retry 없이
    # 곧장 rewrite 로 가버리는 비일관성이 있었습니다(2026-07-24 수정).
    if intent != IntentCategory.COURSE_RECOMMENDATION.value and loop_count < 2:
        print(f"[-] [하이브리드 교정 1차] 수치/단위 교정을 위해 tool_agent 로 직행합니다. (현재 루프: {loop_count})")
        return "direct_retry"

    print(f"[-] [하이브리드 교정 2차 이상] 쿼리 전면 재작성을 수행합니다. (현재 루프: {loop_count})")
    return "rewrite"


def build_agent_graph():
    """LangGraph StateGraph 를 조율하여 멀티 툴 연동 및 하이브리드 자율 교정 루프가 탑재된 RAG 에이전트를 빌드합니다."""
    workflow = StateGraph(AgentState)

    # 1. 그래프 노드 추가
    workflow.add_node("intent_analyzer", analyze_intent_node)
    workflow.add_node("safety_evaluator", evaluate_safety_node)
    workflow.add_node("retriever", retrieve_rag_node)
    workflow.add_node("report_generator", generate_report_node)
    workflow.add_node("quality_checker", check_quality_node)
    workflow.add_node("query_rewriter", rewrite_query_node)
    workflow.add_node("quick_responder", quick_responder_node)
    workflow.add_node("tool_executor", tool_executor_node)
    workflow.add_node("tool_agent", tool_agent_node)

    # 2. 고정 경로 엣지 연결
    workflow.set_entry_point("intent_analyzer")
    
    # 의도 분석 및 해석 결과에 따라 B2B 기획서 생성 또는 Q&A로 단일 분기
    workflow.add_conditional_edges(
        "intent_analyzer",
        route_after_analysis,
        {
            "quick_response": "quick_responder",
            "full_pipeline": "safety_evaluator"
        }
    )
    
    workflow.add_edge("safety_evaluator", "retriever")
    workflow.add_edge("report_generator", "quality_checker")
    
    # quick_responder 의 조기 완료 여부에 따라 바로 종료할지 tool_agent 로 보낼지 분기
    workflow.add_conditional_edges(
        "quick_responder",
        route_after_quick_response,
        {
            "end": END,
            "tool_agent": "tool_agent"
        }
    )
    
    workflow.add_edge("tool_executor", "tool_agent")

    # 2-1-1. 무조건 반려(Fail-Fast): DB 매칭 코스가 0건이면 기획서를 생성하지 않고 반려 경로로.
    # (2026-07-24~25 에는 retriever → report_generator 고정 엣지였습니다 — docent_generator/
    # report_finalizer 통합 시 should_finalize_report 조건부 분기를 없애며 단순화한 것인데,
    # 2026-07-25 무조건 반려 정책 도입으로 다시 조건부가 되었습니다. 판단 기준이 그때의
    # intent_category(항상 참이라 무의미)와 달리 이번엔 실제 검색 결과라는 점이 다릅니다.)
    workflow.add_conditional_edges(
        "retriever",
        route_after_retriever,
        {
            "exit_early": "quick_responder",
            "generate_report": "report_generator"
        }
    )

    # 2-2. tool_agent 툴 호출 루프 연결
    workflow.add_conditional_edges(
        "tool_agent",
        should_call_tools,
        {
            "call_tools": "tool_executor",
            "quality_check": "quality_checker",
            "end": END,
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
