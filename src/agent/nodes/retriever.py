"""RDB 하드 필터 + pgvector 검색(Retriever) 노드와 그 검색-정책 헬퍼.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

from typing import Any, Dict, List

from src.agent.state import AgentState

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


def _crop_location_boost(chunk: Dict[str, Any], key_item_or_crop: str | None, preferred_location: str | None) -> int:
    """작물/지역 소프트 매칭 점수. 정확히 하나의 코스를 미리 알 수 없는 자연어 질의에서
    벡터 유사도 순위를 유지하면서도 언급된 작물/지역이 포함된 코스를 우선 배치하기 위한 보정치입니다.
    """
    boost = 0
    if key_item_or_crop and key_item_or_crop in (chunk.get("crops") or ""):
        boost += 1
    if preferred_location and (
        preferred_location in (chunk.get("administrative_areas") or "")
        or preferred_location in (chunk.get("course_name") or "")
    ):
        boost += 1
    return boost


def _build_fail_fast_result(
    exit_reason: str, market_insight: Dict[str, Any] | None
) -> Dict[str, Any]:
    """무조건 반려(Fail-Fast) 정책의 retrieve_rag_node 조기 종료 반환값을 만듭니다
    (2026-07-25 정책 전환). DB 매칭 코스가 0건인 순간, 그 뒤의 모든 검색(pgvector 유사도 검색,
    culture_crop_knowledge 검색, course_sub_segments 조회)을 전면 중단하고 반려 사유만 실어
    즉시 반환합니다 — 예전의 fail-soft 경로는 필터를 해제하고 전체 코스로 계속 진행해 유료
    임베딩/LLM 호출을 모두 소비한 뒤, 결국 report_generator 가 같은 반려 메시지를 쓰거나
    (더 나쁜 경우) 반려 메시지와 무관한 culture/market 컨텍스트를 quality_checker 가 대조하며
    최대 3회의 재작성 루프를 도는 낭비가 있었습니다.

    fallback_applied/fallback_reason 은 명시적으로 False/None 으로 돌려놓습니다. 이 두 필드는
    "조건을 완화해서 계속 검색했다"는 각주용 신호인데, 이제 완화 자체를 하지 않으므로 여기서
    켜지면 report_generator 가 잘못된 각주를 붙일 수 있습니다.
    market_insight 는 이미 조회를 마친 값이라 그대로 실어 보냅니다(check_quality_node 가
    is_exit_early 로 먼저 단락하므로 이 값이 검증 컨텍스트로 쓰이지는 않습니다).
    """
    print(f"[!] [무조건 반려] {exit_reason} (벡터/문화지식 검색을 실행하지 않고 즉시 종료)")
    return {
        "retrieved_chunks": [],
        "culture_chunks": [],
        "sub_segments": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "market_insight": market_insight,
        "is_exit_early": True,
        "exit_reason": exit_reason,
    }


def retrieve_rag_node(state: AgentState) -> Dict[str, Any]:
    """RDB 메타 필터링과 pgvector 유사도 검색을 조합하여 관련 코스 정보를 조회하는 Retriever 노드입니다.
    올레 코스 정보와 별도로, 제주 밭담문화·작물 생육 지식 DB(culture_crop_knowledge)도
    함께 검색하여 문서 근거가 있는 도슨트 서사를 뒷받침합니다.
    **(2026-07-25 정책 전환 — 무조건 반려/Fail-Fast)** target_course / preferred_location /
    key_item_or_crop 세 필터 중 하나라도 매칭 0건이면, 그 조건만 해제하고 전체 코스에서 계속
    검색하던 기존 fail-soft 동작을 폐지하고 그 자리에서 즉시 검색을 중단합니다
    (`_build_fail_fast_result` 참고).
    """
    from src.agent.nodes import (
        _build_fail_fast_result,
        _crop_location_boost,
        _describe_hard_constraint_zero_match,
        _describe_target_course_mismatch,
        _execute_rdb_filtering,
        _fetch_market_insight,
        _filter_course_ids_by_crop,
        _filter_course_ids_by_location,
        _filter_course_ids_by_target_course,
        _looks_like_course_name,
        _search_culture_knowledge,
        get_solar_embedding,
        get_supabase_client,
    )

    # 0. 기상 위험(DANGER)에 의해 상류 노드(safety_evaluator)가 조기 반려를 선언한 경우 즉시 탈출
    safety = state.get("safety_check") or {}
    is_weather_danger = safety.get("safety_status") == "DANGER"
    print(f"[DEBUG] retrieve_rag_node 진입 - state.is_exit_early: {state.get('is_exit_early')}, is_weather_danger: {is_weather_danger}")
    if state.get("is_exit_early") and is_weather_danger:
        return _build_fail_fast_result(
            state.get("exit_reason") or "안전상의 이유로 기획서를 생성할 수 없습니다.",
            state.get("market_insight"),
        )

    constraints = state["parsed_constraints"] or {}
    safety = state["safety_check"] or {}
    target_course = state.get("target_course")
    b2b_params = state.get("b2b_params") or {}
    key_item_or_crop = b2b_params.get("key_item_or_crop")
    preferred_location = b2b_params.get("preferred_location")
    target_month = b2b_params.get("target_month")

    # preferred_location 에 지역명이 아니라 코스명이 들어온 경우("1코스로 기획서 만들어줘" →
    # intent_parser 가 preferred_location='1코스' 로 채움)를 지역 조건으로 쓰지 않습니다.
    # 코스 지목은 이미 target_course 필터가 처리하며, 코스명을 지역으로 쓰면 (1) 지역 필터가
    # 0건이 되어 "'1코스' 지역과 겹치는 코스가 없다"는 엉뚱한 각주가 붙고, (2) 부분 일치인
    # _crop_location_boost 가 "1코스"를 "10-1코스"의 course_name 에 매칭시켜 전혀 다른 코스를
    # 상위로 끌어올립니다(코스명 접두사 충돌 — _filter_course_ids_by_target_course 가 완전
    # 일치로 바뀐 것과 같은 이유).
    if _looks_like_course_name(preferred_location):
        preferred_location = None

    hard = constraints.get("hard_constraints", {})
    vector_query = constraints.get("vector_query", state["query"])

    # 기상 경보로 인한 안전 우회 쿼리 보정 적용
    if safety.get("reroute_required") and safety.get("alternative_query_override"):
        vector_query = safety["alternative_query_override"]

    client = get_supabase_client()

    # 제주관광공사 방문객 빅데이터(Market Insight) 조회 - 선호 지역/방문월이 있을 때만 시도
    market_insight = None
    if b2b_params.get("include_market_insights", True):
        market_insight = _fetch_market_insight(client, preferred_location, target_month)

    # RDB 기반 필터링 (완화 없이 1회만 단독 실행, 휠체어 등 hard_constraints 만 반영)
    course_ids = _execute_rdb_filtering(client, hard)

    # **(2026-07-27 잔여 갭 수정)** 아래 세 필터(target_course/preferred_location/
    # key_item_or_crop)는 전부 `if <조건> and course_ids:` 로 감싸져 있어, course_ids 가 이미
    # 이 시점에 0건이면(=하드 제약 자체가 0건, 현재 유일한 하드 제약은 휠체어) 셋 다 조건문을
    # 통과 못 해 무조건 반려(is_exit_early)가 한 번도 걸리지 않고 그대로 아래 `if course_ids:`
    # (pgvector 검색)도 건너뛴 채 정상 종료 형태로 빠져나갔습니다. 그 결과 report_generator 의
    # 더 오래된 `if not chunks:` 문구("데이터베이스에서 찾을 수 없었습니다")로만 응답이 만들어져
    # 구체적인 사유가 사라지고, is_exit_early 가 꺼져 있던 탓에 check_quality_node 의 조기
    # 단락도 적용되지 않아 재작성 루프까지 낭비됐습니다(CLAUDE.md "Known remaining gap" — 현재
    # DB엔 휠체어 이용 가능 코스가 10개 있어 실제로는 발생하지 않던 잠재 버그). 이제 다른 세
    # 필터와 동일하게, 이 시점에 이미 0건이면 그 자리에서 즉시 반려합니다.
    if not course_ids:
        exit_reason = _describe_hard_constraint_zero_match(hard) or (
            "지정하신 조건에 맞는 올레 코스 데이터를 찾지 못해 기획서를 생성할 수 없습니다."
        )
        return _build_fail_fast_result(exit_reason, market_insight)

    # B2B 성격상 B2C형 소프트 제약 및 Fallback 완화 로직은 제거됨 (기본값 설정).
    # 아래 세 필터도 2026-07-25 부터 무조건 반려(Fail-Fast)로 전환되어 이 두 값을 True/사유로
    # 바꾸는 경로가 더는 없습니다. 필드 자체는 report_generator 가 여전히 읽으므로(각주 지시문)
    # 정상 경로에서 False/None 을 그대로 내려보내기 위해 남겨 둡니다.
    fallback_applied = False
    fallback_reason = None

    # target_course(질의에 특정 코스가 언급된 경우) 필터. 예전엔 이걸 _execute_rdb_filtering
    # 안에서 courses.course_name 완전 일치(.eq())로 하드 필터링했는데, target_course 가 "1코스"
    # 같은 정식 코스명이 아니라 "가파도"처럼 섬/지명으로 들어오면(라우터가 그렇게 추출할 수 있음)
    # course_name 과 절대 일치하지 않아 후보가 0개가 되는 문제가 있었습니다(2026-07-24 QA 재현:
    # "가파도 코스로 기획서 만들어줘"). 이후 한동안은 조건을 해제하고 전체에서 계속 찾는
    # fail-soft 였지만, 2026-07-25 무조건 반려 정책 전환으로 겹치는 코스가 0건이면 그 자리에서
    # 검색을 중단하고 반려 사유만 돌려줍니다.
    if target_course and course_ids:
        course_ids, target_course_matched = _filter_course_ids_by_target_course(client, course_ids, target_course)
        if not target_course_matched:
            # 사유 문구만 두 갈래로 구분합니다: target_course 가 실존하는 코스인데 하드 제약
            # (휠체어 등) 때문에 후보에서 빠진 경우("2코스는 존재하지만 휠체어 구간이 없음")는
            # 그 정확한 사유를 쓰고, target_course 자체가 DB에 없는 코스명인 경우("가파도")는
            # 코스명을 찾지 못했다는 일반 사유를 씁니다. 어느 쪽이든 다른 코스로 조용히 대체
            # 추천하지 않고 기획서 작성을 중단하는 것은 동일합니다.
            hard_constraint_reason = _describe_target_course_mismatch(client, target_course, hard)
            exit_reason = hard_constraint_reason or (
                f"'{target_course}' 코스명과 직접 일치하는 코스를 찾지 못해 "
                f"기획서를 생성할 수 없습니다."
            )
            return _build_fail_fast_result(exit_reason, market_insight)

    # 지역 조건(preferred_location)을 벡터 검색 이전에 실제 하드 필터로 반영합니다. 이게 없으면
    # 벡터 검색이 먼저 의미상 가장 비슷한 상위 몇 개만 뽑고 그 안에서만 지역 boost를 적용해,
    # 지역과 무관한 코스가 뽑히고도 Market Insight만 엉뚱하게 그 지역 통계를 보여주는 불일치가
    # 생길 수 있습니다(실사용 중 발견됨: "외국인 방문객 1위 지역" 통계는 A 지역인데 실제 추천 코스는
    # 전혀 다른 B 지역인 경우). 겹치는 코스가 하나도 없으면(2026-07-25 무조건 반려 정책) 지역
    # 조건을 해제하고 전체에서 검색하지 않고 즉시 반려합니다.
    if preferred_location and course_ids:
        course_ids, location_matched = _filter_course_ids_by_location(client, course_ids, preferred_location)
        if not location_matched:
            return _build_fail_fast_result(
                f"'{preferred_location}' 지역과 직접 겹치는 올레 코스를 찾지 못해 "
                f"기획서를 생성할 수 없습니다.",
                market_insight,
            )

    # key_item_or_crop(작물/테마)도 지역과 같은 이유로 벡터 검색 이전에 하드 필터로 반영합니다
    # (실사용 중 발견됨: "쪽파" 질의인데 벡터 검색 상위 후보에 쪽파 태그 코스가 없어 로컬
    # 추천에서 쪽파가 한 번도 조회되지 않던 문제). key_item_or_crop 은 "밭담"/"숲길" 같은
    # 비작물 테마어일 수도 있어(intent_parser 참고), courses.crops 에 실제로 등장하는 값일
    # 때만 필터링하고 아니면 조용히 건너뜁니다(_filter_course_ids_by_crop 이 그 경우 matched=True
    # 를 반환) — 테마 질의가 반려되지 않도록 하는 이 구분은 무조건 반려 정책에서 더 중요해졌습니다.
    if key_item_or_crop and course_ids:
        course_ids, crop_matched = _filter_course_ids_by_crop(client, course_ids, key_item_or_crop)
        if not crop_matched:
            return _build_fail_fast_result(
                f"'{key_item_or_crop}' 작물과 직접 겹치는 올레 코스를 찾지 못해 "
                f"기획서를 생성할 수 없습니다.",
                market_insight,
            )

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
            
            # 검색된 청크의 코스 세부 정보 및 메타데이터 결합. 청크 하나마다 개별 try/except 로
            # 격리합니다 — 예전엔 이 for 루프 전체가 바깥 try 안에 있어서, DB의 결측치 하나
            # (예: total_distance_km IS NULL 인 행)가 예외를 일으키면 그 시점까지 처리된 청크만
            # 남고 이후의 멀쩡한 청크들까지 통째로 버려지는 문제가 있었습니다.
            for item in rpc_res.data:
                try:
                    c_res = client.table("courses").select("*").eq("id", item["course_id"]).execute()
                    course_meta = c_res.data[0] if c_res.data else {}

                    chunks_data.append({
                        "chunk_id": item["id"],
                        "course_id": item["course_id"],
                        "course_name": course_meta.get("course_name"),
                        "crops": course_meta.get("crops") or "",
                        "administrative_areas": course_meta.get("administrative_areas") or "",
                        # .get(key, 0.0) 은 키가 아예 없을 때만 기본값을 쓰고, 키는 있는데 값이
                        # NULL(None)인 경우는 그대로 None 을 반환해 float(None) 에서 TypeError 가
                        # 났었습니다 — "or 0.0" 으로 None/누락 둘 다 안전하게 처리합니다.
                        "total_distance_km": float(course_meta.get("total_distance_km") or 0.0),
                        "estimated_time_hours": float(course_meta.get("estimated_time_hours") or 0.0),
                        "estimated_time_text": course_meta.get("estimated_time_text", ""),
                        "difficulty": course_meta.get("difficulty", "중"),
                        "title": item["title"],
                        "content": item["content"],
                        "similarity": item["similarity"]
                    })
                except Exception as e:
                    print(f"[!] 코스 청크(course_id={item.get('course_id')}) 조립 실패, 이 청크만 건너뜁니다: {e}")
        except Exception as e:
            print(f"[!] RAG 벡터 검색 중 예외 발생: {e}")

    # 언급된 작물/지역이 포함된 코스를 유사도 순위를 유지한 채 우선 배치 (안정 정렬)
    if key_item_or_crop or preferred_location:
        chunks_data.sort(key=lambda c: -_crop_location_boost(c, key_item_or_crop, preferred_location))

    # 제주 밭담문화·작물 생육 지식 DB 검색 (외부 API 대신 검증된 문서 기반 근거 확보)
    # culture_crop_knowledge 테이블이 아직 적재되지 않은 경우, 로컬 JSON 문서 검색으로 자동 폴백합니다.
    # 사용자가 작물/테마를 직접 언급하지 않았다면(key_item_or_crop 없음), 범용 vector_query로
    # 문화지식을 검색하는 대신 실제로 선택된 코스의 진짜 crops 로 검색합니다. 그렇지 않으면
    # 실사용 중 발견된 것처럼 섹션 2(도슨트 서사)가 이 코스와 무관한 작물(예: 수박/참외/고사리)을
    # 언급하고, 섹션 3(report_generator)은 그 코스의 실제 crops(예: 보리)만 다뤄서 두 섹션이
    # 서로 다른 작물 얘기를 하는 불일치가 생깁니다. key_item_or_crop 이 있으면(작물이든 "밭담"
    # 같은 비작물 테마든) 사용자가 명시한 의도를 그대로 존중합니다.
    if key_item_or_crop:
        culture_fallback_query = key_item_or_crop
    else:
        course_crops = []
        for c in chunks_data:
            for crop in (c.get("crops") or "").split(","):
                crop = crop.strip()
                if crop and crop not in course_crops:
                    course_crops.append(crop)
        culture_fallback_query = " ".join(course_crops) if course_crops else vector_query
    culture_chunks_data = _search_culture_knowledge(client, key_item_or_crop, culture_fallback_query)

    # 최상위 매칭 코스의 실제 세부 구간(구간명 + 누적 km)을 조회 (B2B 타임라인 표의 근거 데이터)
    sub_segments_data = []
    if chunks_data:
        top_course_id = chunks_data[0]["course_id"]
        try:
            seg_res = (
                client.table("course_sub_segments")
                .select("sub_segment_name,distance_km")
                .eq("course_id", top_course_id)
                .order("distance_km")
                .execute()
            )
            sub_segments_data = seg_res.data or []
        except Exception as e:
            print(f"[!] 세부 구간 데이터 조회 실패 (course_id={top_course_id}): {e}")

    return {
        "retrieved_chunks": chunks_data,
        "culture_chunks": culture_chunks_data,
        "sub_segments": sub_segments_data,
        "fallback_applied": fallback_applied,
        "fallback_reason": fallback_reason,
        "market_insight": market_insight,
        # 정상 경로는 반려가 아님을 명시합니다. 재작성 루프(query_rewriter → retriever)로 이
        # 노드에 재진입했을 때 이전 순회에서 켜진 플래그가 남아있으면, 이번엔 코스를 찾았는데도
        # route_after_retriever 가 반려로 보내버립니다.
        "is_exit_early": False,
        "exit_reason": None,
    }


def _filter_course_ids_by_target_course(
    client: Any, course_ids: List[int], target_course: str
) -> tuple[List[int], bool]:
    """course_ids 중 target_course(질의에 특정 코스가 언급된 경우 라우터가 추출한 값 — "1코스"
    같은 정식 코스명뿐 아니라 "가파도"처럼 섬/지명이 섞여 들어올 수도 있음)와 실제로 겹치는
    코스만 남깁니다. course_name은 반드시 완전 일치("1코스"가 "10-1코스"/"7-1코스"/"14-1코스"/
    "18-1코스"처럼 뒤에 "1코스"로 끝나는 다른 코스와 부분 문자열로 오매칭되는 것을 방지 —
    2026-07-24 QA 중 실제로 "1코스"를 요청했는데 "10-1코스"(가파도)가 선택되는 것으로 재현됨),
    administrative_areas는 지역명이 이런 접두사+하이픈+번호 충돌 패턴이 없어 기존처럼 부분 일치로
    판정합니다. 하나도 안 겹치면(완전 배제 대신) 원래 course_ids 를 그대로 반환하고 두 번째
    반환값을 False 로 표시합니다 — 이후 pgvector 유사도 검색은 코스 본문(가이드북 원문)에 실제로
    언급된 지명까지 의미 기반으로 잡아낼 수 있어, 이 필터가 못 걸러도 최종 결과가 완전히 빗나가지
    않는 경우가 많습니다.
    """
    if not target_course or not course_ids:
        return course_ids, False

    try:
        res = client.table("courses").select("id,administrative_areas,course_name").in_("id", course_ids).execute()
    except Exception as e:
        print(f"[!] 대상 코스 필터링용 코스 조회 실패, 조건 없이 진행합니다: {e}")
        return course_ids, False

    matched_ids = [
        row["id"]
        for row in (res.data or [])
        if target_course == (row.get("course_name") or "") or target_course in (row.get("administrative_areas") or "")
    ]
    if matched_ids:
        return matched_ids, True
    return course_ids, False


def _format_difficulty_labels(levels: List[str]) -> str:
    """허용 난이도 목록을 사유 문구에 넣을 표기로 만듭니다(["상"] → "'상'",
    ["하","중"] → "'하' 또는 '중'"). 목록이 비어 있으면 호출하지 않습니다."""
    return " 또는 ".join(f"'{level}'" for level in levels)


def _describe_hard_constraint_zero_match(hard: dict) -> str | None:
    """RDB 하드 제약 필터(`_execute_rdb_filtering`) 자체가 이 시점에 이미 0건을 반환한 구체적인
    이유를 알아낼 수 있으면 반환합니다. 하드 제약은 `wheelchair_required` 외에 2026-07-27부터
    `max_time_hours`/`max_distance_km`(시간/거리 상한)와 `allowed_difficulties`(허용 난이도
    목록) 3종이 추가되어 총 4종입니다 — 지정된 조건을 전부 모아 한 문장으로 합성하고(예:
    "2.0시간 이내로 다녀올 수 있는 난이도 '상'인 올레 코스가 존재하지 않아 기획서를 생성할 수
    없습니다"), 아무 것도 지정되지 않았으면(예: DB 조회 자체가 실패했거나 courses 테이블이
    비어있는 경우) 원인을 특정할 수 없으므로 None 을 반환해 호출부가 일반 문구를 쓰게 합니다 —
    `_describe_target_course_mismatch` 와 동일한 설계입니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import _normalize_allowed_difficulties

    conditions = []
    if hard.get("wheelchair_required"):
        conditions.append("휠체어로 이용 가능한")
    max_time_hours = hard.get("max_time_hours")
    if max_time_hours is not None:
        conditions.append(f"{max_time_hours}시간 이내로 다녀올 수 있는")
    max_distance_km = hard.get("max_distance_km")
    if max_distance_km is not None:
        conditions.append(f"{max_distance_km}km 이내의")
    allowed_difficulties = _normalize_allowed_difficulties(hard)
    if allowed_difficulties:
        conditions.append(f"난이도 {_format_difficulty_labels(allowed_difficulties)}인")
    if not conditions:
        return None
    return f"{' '.join(conditions)} 올레 코스가 존재하지 않아 기획서를 생성할 수 없습니다."


def _describe_target_course_mismatch(client: Any, target_course: str, hard: dict) -> str | None:
    """target_course가 후보에서 빠진 구체적인 이유를 알아낼 수 있으면 반환합니다. 특히
    target_course가 실제로 존재하는 코스인데 하드 제약(휠체어, 그리고 2026-07-27부터 시간/거리
    상한과 허용 난이도 목록) 때문에 후보에서 제외된 경우("2코스는 실존하지만 휠체어 구간이
    없음", "1코스는 실존하지만 실제 소요시간이 요청한 상한을 초과함", "1코스의 실제 난이도가
    요청한 난이도 목록에 없음"), "그 코스명을 못 찾았다"는 일반 문구 대신
    정확한 사유를 사용자에게 알립니다. **(2026-07-24 수정)** 이 경우 다른 코스로 조용히 대체
    추천하지 않고 기획서 작성 자체를 중단합니다(사용자 요청) — 그래서 이 사유 문구도 "다른
    코스로 대체 추천합니다"가 아니라 "왜 작성할 수 없는지"만 명시해야 합니다. target_course가
    애초에 DB에 없는 코스명이면(예: "가파도") 알아낼 수 없으므로 None을 반환해 호출부가 일반
    문구를 쓰게 합니다.

    휠체어 조건 확인 질의(`course_name,has_wheelchair_segment`)와 시간/거리/난이도 확인 질의
    (`course_name,estimated_time_hours,total_distance_km,difficulty`)는 별도 질의로
    분리합니다 — 기존 휠체어 질의의 select 컬럼 문자열을 그대로 유지해 이를 목킹하는 기존
    테스트 픽스처를 건드리지 않기 위함입니다(2개 조건이 동시에 걸리면 질의도 2번 나가지만,
    이 함수는 fail-fast 반려 경로에서만 호출되는 드문 경로라 감내할 수 있는 비용입니다).
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import _normalize_allowed_difficulties

    reasons = []

    if hard.get("wheelchair_required"):
        try:
            res = (
                client.table("courses")
                .select("course_name,has_wheelchair_segment")
                .eq("course_name", target_course)
                .execute()
            )
        except Exception as e:
            print(f"[!] target_course 휠체어 조건 확인 실패: {e}")
            res = None
        if res and res.data and res.data[0].get("has_wheelchair_segment") != "있음":
            reasons.append("휠체어로 이용 가능한 구간이 없습니다")

    max_time_hours = hard.get("max_time_hours")
    max_distance_km = hard.get("max_distance_km")
    allowed_difficulties = _normalize_allowed_difficulties(hard)
    if (
        max_time_hours is not None
        or max_distance_km is not None
        or allowed_difficulties
    ):
        try:
            res2 = (
                client.table("courses")
                .select("course_name,estimated_time_hours,total_distance_km,difficulty")
                .eq("course_name", target_course)
                .execute()
            )
        except Exception as e:
            print(f"[!] target_course 시간/거리/난이도 조건 확인 실패: {e}")
            res2 = None
        if res2 and res2.data:
            row = res2.data[0]
            actual_time = row.get("estimated_time_hours")
            if max_time_hours is not None and actual_time is not None and actual_time > max_time_hours:
                reasons.append(
                    f"실제 소요시간({actual_time}시간)이 요청하신 {max_time_hours}시간 이내 조건을 초과합니다"
                )
            actual_distance = row.get("total_distance_km")
            if max_distance_km is not None and actual_distance is not None and actual_distance > max_distance_km:
                reasons.append(
                    f"실제 거리({actual_distance}km)가 요청하신 {max_distance_km}km 이내 조건을 초과합니다"
                )
            actual_difficulty = row.get("difficulty")
            if (
                allowed_difficulties
                and actual_difficulty
                and actual_difficulty not in allowed_difficulties
            ):
                reasons.append(
                    f"실제 난이도({actual_difficulty})가 요청하신 난이도 조건"
                    f"({', '.join(allowed_difficulties)})에 포함되지 않습니다"
                )

    if not reasons:
        return None
    return f"'{target_course}'에는 " + ", ".join(reasons) + "."


def _split_comma_tokens(raw: str | None) -> List[str]:
    """쉼표 구분 컬럼(courses.administrative_areas / eup_myeon_dong_areas / crops 등)을
    공백이 제거된 토큰 리스트로 변환합니다."""
    return [token.strip() for token in (raw or "").split(",") if token.strip()]


# _filter_course_ids_by_location 이 courses 에서 읽는 컬럼 목록. eup_myeon_dong_areas 는
# 2026-07-25 에 추가된 컬럼(supabase/schema.sql 섹션 10)이고, 이 리포의 DDL은 Supabase SQL
# 에디터에서 수동으로 실행해야 하므로 아직 ALTER 가 적용되지 않은 환경이 있을 수 있습니다.
# 그 환경에서 조회가 통째로 실패하면(=지역 필터 0건 = 무조건 반려 정책상 즉시 반려) 모든 지역
# 질의가 죽으므로, 실패 시 이 컬럼을 뺀 레거시 목록으로 한 번 더 조회해 예전 매칭 방식으로
# 안전하게 강등됩니다.
_LOCATION_SELECT_COLS = "id,administrative_areas,course_name,eup_myeon_dong_areas"
_LOCATION_SELECT_COLS_LEGACY = "id,administrative_areas,course_name"


def _course_row_matches_location(row: Dict[str, Any], preferred_location: str) -> bool:
    """코스 한 행이 preferred_location 과 겹치는지 계층적으로 판정합니다.

    1) preferred_location 이 course_name 에 등장하면 매칭(기존 동작 유지 — 코스명이 지역
       필드로 새어 들어온 경우는 상류에서 `_looks_like_course_name` 이 이미 걸러냅니다).
    2) preferred_location 이 administrative_areas(법정리/법정동)의 토큰과 **완전 일치**하면
       매칭 — 사용자가 법정리 이름 자체를 직접 지목한 경우("세화리")를 계속 지원합니다.
       예전엔 부분 문자열 검사였는데, 완전 토큰 일치로 바꿔 "용담"이 "용담동"에 걸리는 식의
       부분 매칭을 배제합니다.
    3) 그 외(preferred_location 이 읍/면/동 단위 이름인 일반적인 경우)는 eup_myeon_dong_areas
       토큰과의 완전 일치로만 판정합니다. **이게 이번 변경의 핵심**: 예전엔 읍/면 이름을
       _ADMIN_DONG_TO_LEGAL_DONGS 로 법정리 후보로 역확장해 administrative_areas 에서 부분
       문자열로 찾았는데, 같은 법정리 이름이 서로 다른 읍/면에 동시에 존재해서(예: "세화리"가
       구좌읍과 표선면에 각각 존재) 엉뚱한 지역의 코스가 매칭됐습니다(라이브 재현 2026-07-25:
       "구좌읍 감귤" 질의에 표선면/남원읍의 4코스가 "세화리" 충돌로 편입되고, 그 코스는 crops
       에 감귤이 있어 작물 단계의 무조건 반려까지 우회해버림). 이 컬럼이 채워진 행은 소속
       읍/면/동이 확정된 값이므로, 여기서 안 걸리면 그 코스는 그 지역 코스가 아닙니다.
    4) eup_myeon_dong_areas 가 비어있는(NULL/빈 문자열 — 백필 전이거나 신규 추가된) 행에
       한해서만 예전 법정리 역확장 매칭으로 폴백합니다. 과도기 방어용 경로입니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _ADMIN_DONG_TO_LEGAL_DONGS,
        _normalize_admin_tier_name,
    )

    if preferred_location in (row.get("course_name") or ""):
        return True

    if preferred_location in _split_comma_tokens(row.get("administrative_areas")):
        return True

    eup_myeon_tokens = _split_comma_tokens(row.get("eup_myeon_dong_areas"))
    if eup_myeon_tokens:
        if preferred_location in eup_myeon_tokens:
            return True
        # 접미사를 생략한 표기("한림" = "한림읍")까지만 관대하게 허용합니다. 예전의 부분 문자열
        # 매칭은 이 정도의 관대함을 사실상 제공했고, 무조건 반려 정책 아래에서는 이런 표기 차이가
        # 곧바로 사용자에게 "코스를 찾지 못했다"는 반려로 나타나므로 이 경로만 남깁니다.
        normalized = _normalize_admin_tier_name(preferred_location)
        return any(
            _normalize_admin_tier_name(token) == normalized for token in eup_myeon_tokens
        )

    legacy_candidates = {preferred_location} | set(
        _ADMIN_DONG_TO_LEGAL_DONGS.get(preferred_location, [])
    )
    return any(
        cand in (row.get("administrative_areas") or "") for cand in legacy_candidates
    )


def _filter_course_ids_by_location(
    client: Any, course_ids: List[int], preferred_location: str
) -> tuple[List[int], bool]:
    """course_ids 중 preferred_location(행정동/읍/면 단위 — 직접 지정이든 market_location_resolver
    가 채운 것이든)과 실제로 겹치는 코스만 남깁니다. 판정 규칙은
    `_course_row_matches_location` 참고(2026-07-25 부터 courses.eup_myeon_dong_areas 컬럼 기반
    완전 일치가 기본, 백필 전 행만 예전 법정리 역확장으로 폴백). 겹치는 코스가 하나도 없으면
    (완전 배제 대신) 원래 course_ids 를 그대로 반환하고 두 번째 반환값을 False 로 표시해,
    호출부가 그 사실을 처리할 수 있게 합니다(현재 retrieve_rag_node 는 이 경우 무조건 반려).
    """
    if not preferred_location or not course_ids:
        return course_ids, False

    try:
        res = (
            client.table("courses")
            .select(_LOCATION_SELECT_COLS)
            .in_("id", course_ids)
            .execute()
        )
    except Exception as e:
        print(f"[!] 지역 필터링용 코스 조회 실패({_LOCATION_SELECT_COLS}), 레거시 컬럼으로 재시도합니다: {e}")
        try:
            res = (
                client.table("courses")
                .select(_LOCATION_SELECT_COLS_LEGACY)
                .in_("id", course_ids)
                .execute()
            )
        except Exception as e2:
            print(f"[!] 지역 필터링용 코스 조회 재시도도 실패, 지역 조건 없이 진행합니다: {e2}")
            return course_ids, False

    matched_ids = [
        row["id"]
        for row in (res.data or [])
        if _course_row_matches_location(row, preferred_location)
    ]
    if matched_ids:
        return matched_ids, True
    return course_ids, False


def _filter_course_ids_by_crop(
    client: Any, course_ids: List[int], key_item_or_crop: str
) -> tuple[List[int], bool]:
    """course_ids 중 key_item_or_crop 과 courses.crops 이 실제로 겹치는 코스만 남깁니다.
    key_item_or_crop 은 작물명뿐 아니라 "밭담"/"숲길" 같은 비작물 테마어일 수도 있어(intent_parser
    참고), 그런 테마어는 courses.crops 에 애초에 등장하지 않으므로 필터링을 건너뛰고 두 번째
    반환값을 True(정상, 완화 아님)로 반환합니다 — 테마 질의마다 불필요한 "완화 사유" 각주가 뜨는
    것을 방지합니다. 반대로 실제 작물명인데 이 course_ids 안에 겹치는 코스가 하나도 없으면
    (완전 배제 대신) 원래 course_ids 를 그대로 반환하고 False 를 반환해, 호출부가 "작물 조건을
    해제하고 검색했다"는 사유를 리포트에 남길 수 있게 합니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _get_known_crop_tags,
    )

    if not key_item_or_crop or not course_ids:
        return course_ids, True

    known_crop_tags = _get_known_crop_tags(client)
    if key_item_or_crop not in known_crop_tags:
        return course_ids, False

    try:
        res = client.table("courses").select("id,crops").in_("id", course_ids).execute()
    except Exception as e:
        print(f"[!] 작물 필터링용 코스 조회 실패, 작물 조건 없이 진행합니다: {e}")
        return course_ids, True

    matched_ids = [row["id"] for row in (res.data or []) if key_item_or_crop in (row.get("crops") or "")]
    if matched_ids:
        return matched_ids, True
    return course_ids, False
