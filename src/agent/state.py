from typing import TypedDict, List, Dict, Any, Optional


class AgentState(TypedDict):
    """LangGraph 의 에이전트 그래프 노드 간에 전달되는 상태 스키마입니다."""
    
    # 1. 입력 및 의도 분석 정보
    query: str
    parsed_constraints: Optional[Dict[str, Any]]
    intent_category: Optional[str]
    target_course: Optional[str]
    b2b_params: Optional[Dict[str, Any]]
    # target_course 로 조회한 코스의 DB 실측 메타데이터(거리/소요시간/난이도/시작·종점 등).
    # quick_responder_node 가 채우고 tool_agent_node 가 최종 답변의 근거로 재사용합니다 —
    # 최종 답변은 quick_responder 가 아니라 tool_agent 가 쓰는 경우가 많아서, 여기에 실어
    # 두지 않으면 코스 실측치가 최종 답변에 전달되지 않습니다.
    course_meta: Optional[Dict[str, Any]]

    # 2. 날씨 및 안전 검증 정보
    weather_info: Optional[Dict[str, Any]]
    safety_check: Optional[Dict[str, Any]]

    # 3. 검색 및 완화 정보
    retrieved_chunks: List[Dict[str, Any]]
    culture_chunks: List[Dict[str, Any]]
    sub_segments: List[Dict[str, Any]]
    fallback_applied: bool
    fallback_reason: Optional[str]
    market_insight: Optional[Dict[str, Any]]
    
    # 4. 답변 생성 및 로컬 추천 정보
    docent_answer: Optional[str]
    recommendations: List[Dict[str, Any]]
    final_response: Optional[str]
    
    # 5. 품질 평가 및 자율 순환 제어 정보
    quality_report: Optional[Dict[str, Any]]
    loop_count: int
    tool_calls: Optional[List[Dict[str, Any]]]
    tool_outputs: Optional[List[Dict[str, Any]]]
    tool_depth: Optional[int]

