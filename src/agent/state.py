from typing import TypedDict, List, Dict, Any, Optional


class AgentState(TypedDict):
    """LangGraph 의 에이전트 그래프 노드 간에 전달되는 상태 스키마입니다."""
    
    # 1. 입력 및 의도 분석 정보
    query: str
    parsed_constraints: Optional[Dict[str, Any]]
    intent_category: Optional[str]
    target_course: Optional[str]
    
    # 2. 날씨 및 안전 검증 정보
    weather_info: Optional[Dict[str, Any]]
    safety_check: Optional[Dict[str, Any]]
    
    # 3. 검색 및 완화 정보
    retrieved_chunks: List[Dict[str, Any]]
    fallback_applied: bool
    fallback_reason: Optional[str]
    
    # 4. 답변 생성 및 로컬 추천 정보
    docent_answer: Optional[str]
    recommendations: List[Dict[str, Any]]
    final_response: Optional[str]
    
    # 5. 품질 평가 및 자율 순환 제어 정보
    quality_report: Optional[Dict[str, Any]]
    loop_count: int
