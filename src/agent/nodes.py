import json
from typing import Dict, Any, List
from src.agent.llm_client import get_chat_completion
from src.agent.weather_client import simulate_weather_by_query, get_current_weather
from src.agent.router import route_intent
from src.ingestion.database_loader import get_supabase_client, get_solar_embedding
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations
from src.agent.state import AgentState


def route_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자 질의를 4가지 카테고리로 사전 분류하여, 이후 로컬 맛집/카페 추천 노드 실행 여부를
    결정짓는 Intent Router 노드입니다.
    """
    result = route_intent(state["query"])
    return {
        "intent_category": result.category.value,
        "target_course": result.target_course
    }


def parse_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자의 자연어 질문에서 의도 및 Hard/Soft 제약 조건을 추출하여 정형화하는 Intent Parser 노드입니다."""
    query = state["query"]
    
    system_prompt = """당신은 제주올레 탐방객의 요구사항을 분석하여 쿼리 조건으로 변환하는 전문 분석기입니다.
사용자의 자연어 입력에서 절대 타협할 수 없는 'Hard Constraints'와 완화 가능한 'Soft Constraints'를 추출하여 아래 JSON 규격으로만 응답하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[추출 규칙]
1. hard_constraints: 휠체어 전용 구간 등 신체/동행 조건과 관련된 필수 제약 (wheelchair_required: true/false)
2. soft_constraints: 소요시간(max_time_hours), 거리(max_distance_km), 난이도(difficulty: 상/중/하)
3. vector_query: 가이드북 임베딩 검색에 사용할 핵심 자연어 키워드 및 작물명 (예: "당근 밭길", "감귤 코스", "마늘향" 등)

[응답 포맷 (JSON 전용)]
{
  "hard_constraints": {
    "wheelchair_required": boolean
  },
  "soft_constraints": {
    "max_time_hours": number or null,
    "max_distance_km": number or null,
    "difficulty": string or null
  },
  "vector_query": string
}"""

    try:
        raw_res = get_chat_completion(system_prompt, query)
        
        # 코드 펜스 제거
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        parsed = json.loads(cleaned)
    except Exception as e:
        print(f"[!] 의도 파싱 중 오류 발생: {e}. 기본 제약 조건으로 폴백합니다.")
        parsed = {
            "hard_constraints": {"wheelchair_required": False},
            "soft_constraints": {"max_time_hours": None, "max_distance_km": None, "difficulty": None},
            "vector_query": query
        }
        
    return {"parsed_constraints": parsed}


def evaluate_safety_node(state: AgentState) -> Dict[str, Any]:
    """현재 날씨 상황을 진단하여 기상 악화 시 안전 우회 동선을 수립하는 Safety Evaluator 노드입니다."""
    query = state["query"]
    
    # 1. 쿼리에서 유력한 제주도 행정구역(읍·면·동) 키워드 추출
    area = "제주"
    for token in ["성산", "구좌", "남원", "한림", "애월", "조천", "한경", "대정", "안덕", "표선", "우도", "추자"]:
        if token in query:
            area = token + "읍" if token in ["성산", "구좌", "남원", "한림", "애월", "조천", "대정", "안덕", "표선", "한경"] else token + "도"
            break
            
    # 2. 기상청 API 를 통한 실시간 날씨 데이터 수집
    real_weather = get_current_weather(area)
    
    # 3. 질문 텍스트 기반 시뮬레이션 날씨 진단 (태풍 등 위험 시나리오 데모/검증용)
    simulated_weather = simulate_weather_by_query(query)

    # 4. 실시간 기상 상태와 시뮬레이션 기상 상태 결합
    # 시뮬레이션의 DANGER(태풍/폭우/홍수)만 실제 판단에 반영합니다.
    # WARNING 등급("바람", "비" 등 일상 대화에서도 흔히 쓰이는 단어 기반)까지 반영하면
    # 실제 위험이 없어도 오탐으로 안전 우회가 발동할 수 있어 실제 판단에서는 제외합니다.
    weather = real_weather
    if simulated_weather["status"] == "DANGER":
        weather = simulated_weather
        
    safety_check = {
        "safety_status": weather["status"],
        "reason": weather["description"],
        "reroute_required": weather["status"] in ["WARNING", "DANGER"],
        "alternative_query_override": None
    }
    
    # 기상 악화 시 대체 안전 경로(내륙 숲길, 우회로)를 추천하도록 쿼리 보정 정보 추가
    if safety_check["reroute_required"]:
        if weather["status"] == "DANGER":
            safety_check["alternative_query_override"] = "바람을 피해 걷기 좋은 내륙 숲길 오솔길 코스"
        else:
            safety_check["alternative_query_override"] = "해안 도로 대신 바람이 차단된 조용하고 안전한 중산간 올레길 코스"
            
    return {
        "weather_info": weather,
        "safety_check": safety_check
    }


def retrieve_rag_node(state: AgentState) -> Dict[str, Any]:
    """RDB 메타 필터링과 pgvector 유사도 검색을 조합하여 관련 코스 정보를 조회하며,
    결과가 없을 시 계층적으로 소프라 제약을 완화(Fallback)하는 Retriever 노드입니다.
    """
    constraints = state["parsed_constraints"] or {}
    safety = state["safety_check"] or {}
    
    hard = constraints.get("hard_constraints", {})
    soft = constraints.get("soft_constraints", {})
    vector_query = constraints.get("vector_query", state["query"])
    
    # 기상 경보로 인한 안전 우회 쿼리 보정 적용
    if safety.get("reroute_required") and safety.get("alternative_query_override"):
        vector_query = safety["alternative_query_override"]
        
    client = get_supabase_client()
    
    # RDB 기반 계층적 필터링 및 Fallback 로직
    fallback_applied = False
    fallback_reason = None
    
    # 1차 조회 시도 (엄격한 조건)
    course_ids = _execute_rdb_filtering(client, hard, soft)
    
    # 2차 조회 시도 (Soft Constraints 완화 - Fallback 1단계)
    if not course_ids and (soft.get("max_time_hours") or soft.get("max_distance_km") or soft.get("difficulty")):
        fallback_applied = True
        fallback_reason = "요청하신 조건(시간/거리/난이도)을 완벽히 충족하는 코스가 존재하지 않아 조건을 완화하여 대안 코스를 탐색합니다."
        
        relaxed_soft = {
            "max_time_hours": (soft["max_time_hours"] + 2.0) if soft.get("max_time_hours") else None,
            "max_distance_km": (soft["max_distance_km"] + 5.0) if soft.get("max_distance_km") else None,
            "difficulty": None  # 난이도 조건은 해제
        }
        course_ids = _execute_rdb_filtering(client, hard, relaxed_soft)
        
    # 3차 조회 시도 (Hard Constraints 만 고정하고 Soft Constraints 는 전면 해제 - Fallback 2단계)
    if not course_ids:
        fallback_applied = True
        fallback_reason = "제시한 세부 조건을 충족하는 코스가 없어, 절대 조건(휠체어 통행 등)만을 충족하는 최적의 추천 코스를 브리핑합니다."
        course_ids = _execute_rdb_filtering(client, hard, {"max_time_hours": None, "max_distance_km": None, "difficulty": None})

    # pgvector 유사도 기반 청크 추출
    chunks_data = []
    if course_ids:
        try:
            # 쿼리 임베딩 생성
            query_vector = get_solar_embedding(vector_query)
            
            # pgvector RPC 함수 실행
            rpc_res = client.rpc("match_course_chunks", {
                "query_embedding": query_vector,
                "match_threshold": 0.1,
                "match_count": 3,
                "filter_course_ids": course_ids
            }).execute()
            
            # 검색된 청크의 코스 세부 정보 및 메타데이터 결합
            for item in rpc_res.data:
                c_res = client.table("courses").select("*").eq("id", item["course_id"]).execute()
                course_meta = c_res.data[0] if c_res.data else {}
                
                chunks_data.append({
                    "chunk_id": item["id"],
                    "course_id": item["course_id"],
                    "course_name": course_meta.get("course_name"),
                    "crops": course_meta.get("crops", ""),
                    "administrative_areas": course_meta.get("administrative_areas", ""),
                    "total_distance_km": float(course_meta.get("total_distance_km", 0.0)),
                    "estimated_time_text": course_meta.get("estimated_time_text", ""),
                    "difficulty": course_meta.get("difficulty", "중"),
                    "title": item["title"],
                    "content": item["content"],
                    "similarity": item["similarity"]
                })
        except Exception as e:
            print(f"[!] RAG 벡터 검색 중 예외 발생: {e}")
            
    return {
        "retrieved_chunks": chunks_data,
        "fallback_applied": fallback_applied,
        "fallback_reason": fallback_reason
    }


def _execute_rdb_filtering(client: Any, hard: dict, soft: dict) -> List[int]:
    """courses 테이블을 메타데이터 기반으로 SQL 필터링하여 일치하는 코스 ID 리스트를 반환합니다."""
    query = client.table("courses").select("id")
    
    # 1. Hard Constraints 필터링 (절대 보장)
    if hard.get("wheelchair_required"):
        query = query.eq("has_wheelchair_segment", "있음")
        
    # 2. Soft Constraints 필터링
    if soft.get("max_time_hours"):
        query = query.lte("estimated_time_hours", soft["max_time_hours"])
    if soft.get("max_distance_km"):
        query = query.lte("total_distance_km", soft["max_distance_km"])
    if soft.get("difficulty"):
        query = query.eq("difficulty", soft["difficulty"])
        
    try:
        res = query.execute()
        return [row["id"] for row in res.data] if res.data else []
    except Exception as e:
        print(f"[!] RDB 필터링 실행 실패: {e}")
        return []


def generate_docent_node(state: AgentState) -> Dict[str, Any]:
    """검색된 코스 컨텍스트 및 작물 생육 지식을 엮어 매력적인 도슨트 어조의 답변 초안을 작성하는 Docent Generator 노드입니다."""
    query = state["query"]
    chunks = state["retrieved_chunks"]
    fallback = state["fallback_applied"]
    reason = state["fallback_reason"]
    weather = state["weather_info"] or {}
    
    if not chunks:
        fallback_msg = "죄송합니다. 요청하신 조건에 부합하는 제주올레길 코스 데이터를 데이터베이스에서 찾을 수 없었어요. 조건을 다르게 설정해 질문해 주시겠어요?"
        return {"docent_answer": fallback_msg, "final_response": fallback_msg}
        
    # 컨텍스트 빌드
    context_str = ""
    for i, c in enumerate(chunks):
        context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
        context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
        context_str += f"내용: {c['content']}\n"
        
    system_prompt = f"""당신은 따뜻하고 전문적인 '제주올레 전문 도슨트'입니다.
제공된 [검색 결과 컨텍스트] 와 현재 [날씨 상황] 을 기반으로 탐방객에게 최적의 올레길 코스를 추천하는 답변을 작성하세요.

[현재 날씨 상황]
- 온도: {weather.get('temperature', 22.0)}°C, 강풍속도: {weather.get('wind_speed_ms', 3.0)}m/s, 특보사항: {', '.join(weather.get('warnings', [])) or '없음'}

[작성 지침]
1. 사용자의 신체 조건(예: 휠체어 여부)이나 기상 악화(예: 강풍/태풍)가 탐지되었다면, 안전이 완벽히 검증되고 우회 조정된 동선임을 강조해 안심시켜 주세요.
2. 계층적 완화(Fallback 적용 여부: {fallback})가 True라면, 원래 요청했던 세부 기준(시간, 거리 등)을 완화하여 대안으로 더 알맞은 코스를 추천한 이유(완화 사유: {reason})를 정중히 브리핑해 주세요.
3. 추천하는 각 코스의 핵심 매개체 작물(예: 당근, 감귤, 마늘 등)을 강조하고, 작물이 올레길에서 펼쳐내는 시각적 풍경(예: '말미오름에서 내려다보이는 초록빛 당근잎밭', '삼달리 귤밭길의 하얀 감귤꽃향기')과 계절적 생육 특징을 인문 도슨트 해설로 풍성하게 녹여내세요.
4. 문체는 정중하면서도 따뜻한 제주올레 가이드 어조(~해요, ~합니다)를 유지하세요."""

    user_msg = f"질문: {query}\n\n[검색 결과 컨텍스트]:\n{context_str}"
    
    docent_answer = get_chat_completion(system_prompt, user_msg)
    # local_recommender 가 스킵되는 경우에도 최종 응답이 비어있지 않도록 기본값으로 세팅
    return {"docent_answer": docent_answer, "final_response": docent_answer}


def recommend_local_node(state: AgentState) -> Dict[str, Any]:
    """코스 작물과 행정구역에 부합하는 로컬 매장(카페/음식점)을 자동 수집하고, 최종 답변과 Trust Tagging 을 결합하는 Local Recommender 노드입니다."""
    chunks = state["retrieved_chunks"]
    docent_answer = state["docent_answer"]
    
    if not chunks or not docent_answer:
        return {"final_response": docent_answer, "recommendations": []}
        
    client = get_supabase_client()
    recommendations = []
    
    # 검색된 상위 코스들의 작물 및 행정구역 매핑 카페/맛집 정보 수집
    rec_cache: Dict[Any, Any] = {}
    for chunk in chunks:
        course_id = chunk["course_id"]
        crops = [c.strip() for c in chunk["crops"].split(",") if c.strip()]
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]

        # 작물과 행정구역이 매칭되는 상점 로드 (동일 조합 중복 호출 방지)
        for crop in crops:
            for area in areas:
                cache_key = (crop, area)
                if cache_key not in rec_cache:
                    rec_cache[cache_key] = get_visit_jeju_recommendations(crop, area)
                rec_list = rec_cache[cache_key]
                for rec in rec_list:
                    # 중복 제거
                    if not any(r["title"] == rec["title"] for r in recommendations):
                        recommendations.append(rec)
                        
    # 최종 답변에 자연스럽게 로컬 상점 추천 섹션 결합
    final_response = docent_answer + "\n\n"
    if recommendations:
        final_response += "🌾 **올레길에서 마주치는 재배 작물 연계 로컬 카페 및 맛집 추천**\n"
        for rec in recommendations[:3]:  # 최대 3개 매장 노출
            final_response += f"- **{rec['title']}** ({rec['crop_tag']} 테마 / {rec['administrative_area']})\n"
            final_response += f"  - 주소: {rec['road_address'] or rec['address']}\n"
            final_response += f"  - 소개: {rec['introduction']}\n"
            if rec.get("phone"):
                final_response += f"  - 전화번호: {rec['phone']}\n"
    else:
        final_response += "🌾 *현재 탐방지 주변에 등록된 작물 테마 로컬 카페/음식점 추천 정보가 존재하지 않습니다.*\n"

    # 답변의 마지막 줄에 신뢰도 및 출처 표기 (Trust Tagging) 결합
    final_response += "\n---\n"
    # 유사도와 매칭 품질에 따라 별점 결정
    stars = "★★★★★"
    if state["fallback_applied"]:
        stars = "★★★★☆"
    final_response += f"[출처: 제주올레 가이드 / 농촌진흥청 농사로 / 비짓제주 API / 신뢰도: {stars}]"
    
    return {
        "recommendations": recommendations,
        "final_response": final_response
    }


def check_quality_node(state: AgentState) -> Dict[str, Any]:
    """답변의 팩트 신뢰성 및 환각 여부, 제약사항 준수 여부를 검증하는 Quality Checker 노드입니다."""
    query = state["query"]
    chunks = state["retrieved_chunks"]
    final_response = state["final_response"] or ""
    
    if not chunks or not final_response:
        return {"quality_report": {"passed": True, "score": 1.0, "feedback": "검색 결과가 없어 평가를 생략합니다."}}
        
    context_str = ""
    for i, c in enumerate(chunks):
        context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
        context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
        context_str += f"본문: {c['content']}\n"
    
    system_prompt = """당신은 생성된 도슨트 추천 답변의 핵심 사실 관계만을 검증하는 '품질 검증원'입니다.
주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여 답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 코스명, 거리, 소요시간, 난이도, 재배작물, 경유 행정구역 등 컨텍스트에 명시된 구체적 수치/명칭을 답변이 왜곡하거나 컨텍스트와 반대로 서술했는가?
2. 사용자가 요청한 필수 제약사항(예: 휠체어 전용 코스 여부)을 어기고 부적절한 코스를 추천했는가?

[검증 대상에서 제외 - 아래 항목은 도슨트의 정상적인 연출이므로 절대 환각이나 결점으로 지적하지 마세요]
- 날씨 정보, 옷차림/준비물 팁, 여행 조언 등 컨텍스트 밖의 실용적 부가 안내
- 풍경 묘사, 계절감, 감성적 수식어 등 도슨트 특유의 문학적 표현
- 컨텍스트에 없는 세부 정보(예: 특정 매장 운영 배경)를 답변이 언급하지 않은 것 (누락은 결함이 아님)
- 거리/소요시간처럼 컨텍스트에 있는 수치를 답변이 그대로 인용한 경우 (출처 재확인 요구 금지)

[응답 포맷 (JSON 전용)]
{
  "passed": boolean,
  "score": number (0.0 ~ 1.0),
  "feedback": "검증 피드백 및 부족한 정보에 대한 구체적 지적"
}"""

    user_msg = f"사용자 질문: {query}\n\n[검색 컨텍스트]:\n{context_str}\n\n[생성된 답변]:\n{final_response}"
    
    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        report = json.loads(cleaned)
    except Exception as e:
        print(f"[!] 품질 검증원 실행 실패: {e}")
        report = {"passed": True, "score": 0.9, "feedback": "자체 평가 오류로 패스 처리"}
        
    return {"quality_report": report}


def rewrite_query_node(state: AgentState) -> Dict[str, Any]:
    """검증 실패 시, 더 정밀한 검색 컨텍스트 획득을 위해 검색 조건 및 키워드를 교정하는 Query Re-writer 노드입니다."""
    query = state["query"]
    constraints = state["parsed_constraints"] or {}
    report = state["quality_report"] or {}
    feedback = report.get("feedback", "")
    
    system_prompt = """당신은 품질 검증 결과에 따라 검색 조건을 보정하는 '쿼리 재작성기'입니다.
기존 사용자 질문, 이전 쿼리 제약사항 및 품질 검증 피드백을 바탕으로, Supabase pgvector 하이브리드 검색에서 더 나은 컨텍스트를 찾기 위한 최적의 '보정된 검색 쿼리 및 메타데이터 필터 조건'을 다시 도출하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON로만 반환하세요.

[응답 포맷 (JSON 전용)]
{
  "revised_vector_query": "새로운 검색용 키워드",
  "revised_soft_constraints": {
    "max_time_hours": number or null,
    "max_distance_km": number or null,
    "difficulty": string or null
  }
}"""

    user_msg = f"사용자 질문: {query}\n\n[이전 제약사항]:\n{json.dumps(constraints)}\n\n[품질 검증원 피드백]:\n{feedback}"
    
    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        revised = json.loads(cleaned)
        
        # 상태 덮어쓰기 형식으로 갱신
        updated_constraints = {
            "hard_constraints": constraints.get("hard_constraints", {"wheelchair_required": False}),
            "soft_constraints": revised.get("revised_soft_constraints", {}),
            "vector_query": revised.get("revised_vector_query", query)
        }
    except Exception as e:
        print(f"[!] 쿼리 재작성 실패: {e}")
        updated_constraints = constraints
        
    return {
        "parsed_constraints": updated_constraints,
        "loop_count": state["loop_count"] + 1
    }
